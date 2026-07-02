"""Protocol for read-only warehouse queries (the text-to-SQL agent).

Ported from core-agent ``ports/read_only_warehouse.py`` — dialect-agnostic,
so the contract is unchanged. Production adapter here is ``DuckDbQueryExecutor``.
"""

import dataclasses
from typing import Any, Protocol, runtime_checkable


@dataclasses.dataclass(frozen=True, slots=True)
class QueryResult:
    """Immutable container for warehouse query output."""

    columns: tuple[str, ...]
    rows: list[tuple[Any, ...]]


@runtime_checkable
class ReadOnlyWarehouseQuery(Protocol):
    """Contract for the text-to-SQL agent's read-only queries.

    Production adapter: ``DuckDbQueryExecutor``.
    """

    def execute_query(self, query: str) -> QueryResult:
        """Execute a read-only *query*."""
        ...
