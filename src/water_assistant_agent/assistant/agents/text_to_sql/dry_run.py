"""DuckDB EXPLAIN-based dry-run validation for the text-to-SQL agent.

Adapted from core-agent ``analytic_table_agent/dry_run.py``. Returns ``None`` on
success or a traceback string on retryable failure (binder/catalog/parse errors
the fixer can attempt to repair). Connection errors are raised directly.
"""

import asyncio
import traceback

import duckdb

from water_assistant_agent.assistant.tools.warehouse import get_duckdb_executor


class DuckDbExplainValidator:
    """``SqlValidator`` implementation for DuckDB EXPLAIN-based validation."""

    async def avalidate(self, sql: str) -> str | None:
        """Run ``EXPLAIN`` on *sql* via DuckDB."""
        executor = get_duckdb_executor()
        try:
            await asyncio.to_thread(executor.execute_query, f"EXPLAIN {sql}")
        except (duckdb.IOException, duckdb.ConnectionException):
            raise
        except Exception as exc:
            return "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__),
            )
        return None
