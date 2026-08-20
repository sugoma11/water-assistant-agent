"""DuckDB query-execution tool for the text-to-SQL agent.

Adapted from core-agent ``tools/analytic_table.py``: tenant guard and Trino
wiring removed, dialect switched to DuckDB.

The tool is produced by :func:`make_query_database_tool`, which binds it to one
executor and one clock. Production builds the module-level default from the same
factory (settings executor, site clock); the harness builds one per case from
``ctx.db`` and the case's frozen clock, so the sub-agent reads the as-of views
and nothing else (``agent_architecture.md`` §3.1, §5).
"""

import asyncio
import functools
import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import sqlglot
import structlog
from google.adk.tools.tool_context import ToolContext

from water_assistant_agent.assistant.agents.text_to_sql.executor import (
    DuckDbQueryExecutor,
    create_duckdb_connection,
)
from water_assistant_agent.assistant.context import Clock
from water_assistant_agent.assistant.ports import ReadOnlyWarehouseQuery
from water_assistant_agent.assistant.settings import get_settings
from water_assistant_agent.assistant.tools.site import site_now

if TYPE_CHECKING:
    from water_assistant_agent.assistant.ports import QueryResult

logger = structlog.get_logger(__name__)

_STATUS = "status"
_ERROR = "error"
_SUCCESS = "success"
_ERROR_DETAILS = "error_details"
_MAX_QUERY_ROWS = 100
_READ_ONLY_QUERY_MSG = "Read-only mode only supports SELECT query expressions."
_TRUNCATED = "result_is_likely_truncated"

# Session-state key under which a successful query stashes its executed result so
# the ``TextToSqlAgentTool`` wrapper can merge it into the tool result surfaced to
# the chat (D4, FR15). It is a plain (session-scoped) key on purpose: the wrapper
# reads it via ``AgentTool``'s state-delta forwarding, and a ``temp:`` key would be
# trimmed from the event delta before that forwarding runs. A single slot,
# overwritten per query and bounded by the 100-row cap, so persistence is cheap;
# what the chat actually replays is the enriched tool-result event, not this state.
QUERY_RESULT_STATE_KEY = "text_to_sql_query_result"

QueryDatabaseTool = Callable[[str, "ToolContext | None"], Awaitable[dict[str, Any]]]
"""What :func:`make_query_database_tool` returns: the ADK-facing query tool."""


class ExecutorHolder:
    """Module-level singleton holder for the DuckDbQueryExecutor."""

    instance: DuckDbQueryExecutor | None = None


def get_duckdb_executor() -> DuckDbQueryExecutor:
    """Lazily create and cache a module-level DuckDbQueryExecutor singleton."""
    if ExecutorHolder.instance is None:
        factory = functools.partial(
            create_duckdb_connection,
            db_path=get_settings().duckdb_path,
        )
        ExecutorHolder.instance = DuckDbQueryExecutor(connection_factory=factory)
    return ExecutorHolder.instance


class _SettingsExecutor:
    """The production executor as a :class:`ReadOnlyWarehouseQuery`, resolved per query.

    :func:`make_query_database_tool` takes an executor *object*, but the
    module-level default below is built while this module is still importing and
    ``DuckDbQueryExecutor`` opens its connection in ``__init__``. Deferring the
    lookup to the first query keeps the settings path exactly as lazy as it was
    before the factory existed — importing this module still touches no database.
    """

    def execute_query(self, query: str) -> "QueryResult":
        """Execute *query* against the settings-built DuckDB singleton."""
        return get_duckdb_executor().execute_query(query)


SETTINGS_EXECUTOR = _SettingsExecutor()
"""The production DB binding: the whole pinned file, unbounded by any ``as_of``."""


def _json_safe_value(candidate: Any) -> Any:
    try:
        json.dumps(candidate)
    except Exception:
        return str(candidate)
    return candidate


def _build_error(details: str) -> dict[str, str]:
    return {_STATUS: _ERROR, _ERROR_DETAILS: details}


def _serialize_rows(query_result: "QueryResult") -> list[dict[str, Any]]:
    return [
        {
            col: _json_safe_value(cell)
            for col, cell in zip(query_result.columns, row, strict=True)
        }
        for row in query_result.rows[:_MAX_QUERY_ROWS]
    ]


def make_query_database_tool(
    executor: ReadOnlyWarehouseQuery,
    clock: Clock,
) -> QueryDatabaseTool:
    """Build the inner query tool bound to *executor* and *clock*.

    The returned callable keeps the **exact** name, signature and docstring of
    the production tool, because all three are load-bearing text: ADK derives the
    tool declaration from the function, the sub-agent's frozen instruction names
    ``query_database_tool``, and §3.1's "frozen means the text, not the objects"
    is only true if rebuilding the object leaves every string identical. The
    binding is the only thing that varies between two contexts.

    *clock* is handed to the SQL pass on every call rather than read once at
    construction, so production's advancing clock and a case's frozen one take
    the identical path (§5's generated-SQL bullet).
    """

    async def query_database_tool(
        sql_query: str,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Run a DuckDB SQL query against the water database and return the result.

        Args:
            sql_query: DuckDB SQL query to execute (SELECT-only).

        Returns:
            dict: ``status`` plus either ``columns``/``rows`` on success or
            ``error_details`` on failure. Results are capped at 100 rows.

        ``tool_context`` is injected by ADK (excluded from the LLM-visible schema, so
        the prompt is untouched) and is ``None`` for direct/eval callers, which keeps
        this tool's behaviour identical outside the agent. On success it stashes the
        executed query's result into session state (D4) for the ``TextToSqlAgentTool``
        wrapper to surface to the chat (FR15).
        """
        result = await _validated_execute(sql_query, executor, clock)
        if tool_context is not None and result.get(_STATUS) == _SUCCESS:
            tool_context.state[QUERY_RESULT_STATE_KEY] = {
                "sql_executed": sql_query,
                "columns": result.get("columns", []),
                "rows": result.get("rows", []),
                _TRUNCATED: result.get(_TRUNCATED, False),
            }
        return result

    # The closure is otherwise indistinguishable from a module-level `def`; ADK
    # reads `__name__`, but a `<locals>` qualname would still surface in logs.
    query_database_tool.__qualname__ = "query_database_tool"
    return query_database_tool


async def _validated_execute(
    sql_query: str,
    executor: ReadOnlyWarehouseQuery,
    clock: Clock,
) -> dict[str, Any]:
    """Parse, guard read-only, and execute a DuckDB SQL query."""
    try:
        parsed = sqlglot.parse_one(sql_query, read="duckdb")
    except sqlglot.errors.SqlglotError:
        logger.warning("Failed to parse SQL query", sql_query=sql_query)
        return _build_error("Failed to parse the SQL query.")

    if not isinstance(parsed, sqlglot.exp.Query):
        return _build_error(_READ_ONLY_QUERY_MSG)

    return await _execute_and_serialize(sql_query, executor)


async def _execute_and_serialize(
    sql_query: str,
    executor: ReadOnlyWarehouseQuery,
) -> dict[str, Any]:
    """Execute query via DuckDB and serialize the output."""
    logger.debug("Executing DuckDB query", sql_query=sql_query)
    try:
        query_output = await asyncio.to_thread(
            executor.execute_query,
            sql_query,
        )
    except Exception as exc:
        logger.exception("DuckDB query execution failed")
        return _build_error(f"Failed to execute the query against DuckDB: {exc}")

    rows = _serialize_rows(query_output)
    response: dict[str, Any] = {
        _STATUS: _SUCCESS,
        "columns": list(query_output.columns),
        "rows": rows,
    }
    if len(query_output.rows) > _MAX_QUERY_ROWS:
        response[_TRUNCATED] = True

    logger.debug("DuckDB query succeeded", row_count=len(rows))
    return response


query_database_tool = make_query_database_tool(SETTINGS_EXECUTOR, site_now)
"""The production default — the settings executor and the site clock.

Built by the same factory the harness calls, so there is one construction path
and the production tool cannot drift from a context-bound one.
"""
