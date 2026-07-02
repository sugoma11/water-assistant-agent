"""NL -> DuckDB SQL builder.

Reuses the repo's existing multi-table text-to-SQL core (``text2sql/core.py``
prompts + ``clean_sql``) and the green-roof semantic layer schema
(``tenants/green_roof/sensordata.py``). This replaces core-agent's heavier
``builders.py`` / ``run_text_to_sql.py`` (single-table id-name/priority logic).
"""

import dataclasses
from functools import lru_cache

import litellm
import structlog

from water_assistant_agent.assistant.prompts.messages import PromptMessage
from water_assistant_agent.assistant.settings import get_settings
from water_assistant_agent.tenants.green_roof.sensordata import table_schema_dict
from water_assistant_agent.text2sql.core import (
    SYSTEM_PROMPT_TEMPLATE,
    USER_PROMPT_TEMPLATE,
    clean_sql,
    format_schema_for_prompt,
)

logger = structlog.get_logger(__name__)


@dataclasses.dataclass(frozen=True, slots=True)
class BuilderResult:
    """A generated DuckDB query plus any model reasoning."""

    sql: str
    reasoning: str


@lru_cache(maxsize=1)
def formatted_schema() -> str:
    """Human-readable multi-table schema block for the builder prompt."""
    return format_schema_for_prompt(table_schema_dict)


async def abuild_sql(user_statement: str) -> BuilderResult:
    """Generate a DuckDB SQL query answering *user_statement*."""
    settings = get_settings()
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(schema=formatted_schema())
    user_prompt = USER_PROMPT_TEMPLATE.format(question=user_statement)
    messages = [
        PromptMessage(role="system", content=system_prompt).to_dict(),
        PromptMessage(role="user", content=user_prompt).to_dict(),
    ]
    completion_result = await litellm.acompletion(
        model=settings.sql_builder_model,
        messages=messages,
        **settings.litellm_extra(),
    )
    message = completion_result.choices[0]["message"]
    reasoning = message.get("reasoning_content", "") or ""
    sql = clean_sql(message["content"])
    logger.debug("Builder generated DuckDB SQL", sql=sql)
    return BuilderResult(sql=sql, reasoning=reasoning)
