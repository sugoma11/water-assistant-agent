"""DuckDB query-execution tool for the text-to-SQL agent.

Adapted from core-agent ``tools/analytic_table.py``: tenant guard and Trino
wiring removed, dialect switched to DuckDB.
"""

import asyncio
import functools
import json
from typing import TYPE_CHECKING, Any

import sqlglot
import structlog

from water_assistant_agent.assistant.agents.text_to_sql.executor import (
    DuckDbQueryExecutor,
    create_duckdb_connection,
)
from water_assistant_agent.assistant.settings import get_settings

if TYPE_CHECKING:
    from water_assistant_agent.assistant.ports import QueryResult

logger = structlog.get_logger(__name__)

_STATUS = "status"
_ERROR = "error"
_SUCCESS = "success"
_ERROR_DETAILS = "error_details"
_MAX_QUERY_ROWS = 100
_READ_ONLY_QUERY_MSG = "Read-only mode only supports SELECT query expressions."


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


async def query_database_tool(sql_query: str) -> dict[str, Any]:
    """Run a DuckDB SQL query against the water database and return the result.

    Args:
        sql_query: DuckDB SQL query to execute (SELECT-only).

    Returns:
        dict: ``status`` plus either ``columns``/``rows`` on success or
        ``error_details`` on failure. Results are capped at 100 rows.
    """
    return await _validated_execute(sql_query)


async def _validated_execute(sql_query: str) -> dict[str, Any]:
    """Parse, guard read-only, and execute a DuckDB SQL query."""
    try:
        parsed = sqlglot.parse_one(sql_query, read="duckdb")
    except sqlglot.errors.SqlglotError:
        logger.warning("Failed to parse SQL query", sql_query=sql_query)
        return _build_error("Failed to parse the SQL query.")

    if not isinstance(parsed, sqlglot.exp.Query):
        return _build_error(_READ_ONLY_QUERY_MSG)

    return await _execute_and_serialize(sql_query)


async def _execute_and_serialize(sql_query: str) -> dict[str, Any]:
    """Execute query via DuckDB and serialize the output."""
    logger.debug("Executing DuckDB query", sql_query=sql_query)
    try:
        query_output = await asyncio.to_thread(
            get_duckdb_executor().execute_query,
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
        response["result_is_likely_truncated"] = True

    logger.debug("DuckDB query succeeded", row_count=len(rows))
    return response
