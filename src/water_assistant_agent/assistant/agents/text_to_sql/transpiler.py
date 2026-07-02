"""DuckDB SQL normalization / parse-validation via sqlglot.

Adapted from core-agent ``analytic_table_agent/transpiler.py``. The builder
already emits DuckDB-dialect SQL (see ``text2sql/core.py``), so this stage is a
*normalize + parse-validate* round-trip (read=duckdb, write=duckdb) rather than a
cross-dialect port. When a schema is supplied, ``sqlglot.optimize`` infers column
types and inserts explicit CASTs (e.g. string literals vs TIMESTAMP columns).

``build_sqlglot_schema`` is generalized to the water DB's **multiple** tables.
"""

from typing import Any

import sqlglot
import structlog
from sqlglot.errors import ErrorLevel
from sqlglot.optimizer import optimize
from sqlglot.schema import MappingSchema

logger = structlog.get_logger(__name__)

_DUCKDB_DIALECT = "duckdb"


def build_sqlglot_schema(table_schema: list[dict[str, Any]]) -> MappingSchema:
    """Build a :class:`MappingSchema` from the multi-table water schema.

    *table_schema* is the ``table_schema_dict`` shape from
    ``tenants/green_roof/sensordata.py``: a list of
    ``{"table_name": str, "columns": [{"name", "type", ...}]}``.
    """
    mapping: dict[str, dict[str, str]] = {}
    for table in table_schema:
        columns = {
            col["name"]: col.get("type", "VARCHAR") for col in table["columns"]
        }
        mapping[table["table_name"]] = columns
    return MappingSchema(mapping, dialect=_DUCKDB_DIALECT)


def _sanitize_query(raw_query: str) -> str:
    """Remove backticks and ANSI escape codes from a raw SQL string."""
    return raw_query.replace("`", '"').replace("[4m", "").replace("[0m", "")


def normalize_duckdb_sql(
    duckdb_query: str,
    schema: MappingSchema | None = None,
) -> str:
    """Parse a DuckDB SQL string and re-render it, raising on parse errors."""
    sanitized = _sanitize_query(duckdb_query)
    tree = sqlglot.parse_one(
        sanitized,
        read=_DUCKDB_DIALECT,
        error_level=ErrorLevel.IMMEDIATE,
    )

    if schema is not None:
        try:
            tree = optimize(tree, schema=schema, dialect=_DUCKDB_DIALECT)
        except Exception:
            logger.exception("Schema-aware optimization failed, using plain render")

    return tree.sql(dialect=_DUCKDB_DIALECT)


class DuckDbSqlTranspiler:
    """``SqlTranspiler`` implementation for DuckDB (normalize round-trip)."""

    def __init__(self, sqlglot_schema: MappingSchema | None = None) -> None:
        self._schema = sqlglot_schema

    def transpile(self, postgresql_sql: str) -> str:
        """Normalize DuckDB SQL, raising on parse errors.

        The parameter name is kept as ``postgresql_sql`` to satisfy the
        ``SqlTranspiler`` Protocol; the input here is already DuckDB SQL.
        """
        return normalize_duckdb_sql(postgresql_sql, schema=self._schema)
