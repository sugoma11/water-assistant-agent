"""Lightweight DuckDB query executor for the text-to-SQL agent.

Mirrors core-agent's ``TrinoQueryExecutor`` (same ``QueryResult`` contract and
reconnect-once-on-connection-error shape), backed by a read-only DuckDB
connection to a local database file.
"""

from collections.abc import Callable

import duckdb
import structlog

from water_assistant_agent.assistant.ports import QueryResult

logger = structlog.get_logger(__name__)

ConnectionFactory = Callable[[], duckdb.DuckDBPyConnection]
"""Callable that returns a fresh DuckDB connection on each invocation."""


class DuckDbQueryExecutor:
    """Execute read-only queries against DuckDB for the text-to-SQL agent."""

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory
        self._connection = connection_factory()

    def execute_query(self, query: str) -> QueryResult:
        """Execute *query* and return column names with rows.

        On a connection-related error the connection is recreated and the
        query is retried exactly once. Query errors (syntax, unknown column)
        are raised immediately.
        """
        try:
            return self._run(query)
        except (duckdb.IOException, duckdb.ConnectionException):
            logger.warning(
                "DuckDB connection error, reconnecting and retrying",
                query=query,
            )
            self._connection = self._connection_factory()
            return self._run(query)

    def _run(self, query: str) -> QueryResult:
        cursor = self._connection.cursor()
        try:
            cursor.execute(query)
            columns: tuple[str, ...] = tuple(
                desc[0] for desc in (cursor.description or [])
            )
            rows = cursor.fetchall()
            return QueryResult(columns=columns, rows=rows)
        finally:
            cursor.close()


def create_duckdb_connection(db_path: str) -> duckdb.DuckDBPyConnection:
    """Open a read-only DuckDB connection to *db_path*.

    Intended to be wrapped in a ``functools.partial`` or lambda and passed to
    ``DuckDbQueryExecutor`` as its ``connection_factory``.
    """
    return duckdb.connect(database=db_path, read_only=True)
