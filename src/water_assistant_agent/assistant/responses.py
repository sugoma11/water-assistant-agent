"""Structured LLM response schemas for the assistant.

Only the generic fixer schema is ported from core-agent; the BigQuery/Trino/
Postgres builder schemas are dropped (the builder here returns plain DuckDB SQL,
see ``agents/text_to_sql/builder.py``).
"""

from pydantic import BaseModel


class FixerAgentResponse(BaseModel):
    """Schema for generic SQL-fixer responses."""

    correct_sql: str
