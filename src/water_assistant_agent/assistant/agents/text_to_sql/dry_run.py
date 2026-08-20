"""DuckDB EXPLAIN-based dry-run validation for the text-to-SQL agent.

Adapted from core-agent ``analytic_table_agent/dry_run.py``. Returns ``None`` on
success or a traceback string on retryable failure (binder/catalog/parse errors
the fixer can attempt to repair). Connection errors are raised directly.

The executor is a constructor argument rather than the warehouse singleton this
module used to import: ``EXPLAIN`` binds names against a catalog, so a validator
on the unbounded connection would accept SQL the case's own executor cannot run —
and the sub-agent is meant to have exactly one DB seam
(``agent_architecture.md`` §3.1).
"""

import asyncio
import traceback

import duckdb

from water_assistant_agent.assistant.ports import ReadOnlyWarehouseQuery


class DuckDbExplainValidator:
    """``SqlValidator`` implementation for DuckDB EXPLAIN-based validation."""

    def __init__(self, executor: ReadOnlyWarehouseQuery) -> None:
        self._executor = executor

    async def avalidate(self, sql: str) -> str | None:
        """Run ``EXPLAIN`` on *sql* via DuckDB."""
        try:
            await asyncio.to_thread(self._executor.execute_query, f"EXPLAIN {sql}")
        except (duckdb.IOException, duckdb.ConnectionException):
            raise
        except Exception as exc:
            return "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__),
            )
        return None
