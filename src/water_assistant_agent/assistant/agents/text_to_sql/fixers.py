"""LLM-assisted SQL fixer and Protocol-conforming wrapper.

Adapted from core-agent ``agents/shared/fixers.py``: model comes from settings
(``sql_fixer_model``) instead of a hardcoded constant, dialect is DuckDB, and a
plain-text fallback is used when a provider lacks structured-output support.
"""

import litellm
import structlog

from water_assistant_agent.assistant.prompts.fixer import (
    FIXER_SYSTEM_PROMPT_TEMPLATE,
    USER_MSG_FOR_FIX_TEMPLATE,
)
from water_assistant_agent.assistant.prompts.messages import PromptMessage
from water_assistant_agent.assistant.responses import FixerAgentResponse
from water_assistant_agent.assistant.settings import get_settings
from water_assistant_agent.text2sql.core import clean_sql

logger = structlog.get_logger(__name__)


def build_fix_user_message(
    user_statement: str,
    incorrect_sql: str,
    error_message: str,
    table_schema: str,
    table_name: str,
) -> str:
    return USER_MSG_FOR_FIX_TEMPLATE.format(
        user_statement=user_statement,
        incorrect_sql=incorrect_sql,
        error_message=error_message,
        table_name=table_name,
        table_schema=table_schema,
    )


async def arun_fixer_completion(system_prompt: str, user_message: str) -> str:
    settings = get_settings()
    messages = [
        PromptMessage(role="system", content=system_prompt).to_dict(),
        PromptMessage(role="user", content=user_message).to_dict(),
    ]
    completion_result = await litellm.acompletion(
        model=settings.sql_fixer_model,
        messages=messages,
        response_format=FixerAgentResponse,
        **settings.litellm_extra(settings.sql_fixer_model),
    )
    content = completion_result.choices[0]["message"]["content"]
    try:
        return FixerAgentResponse.model_validate_json(content).correct_sql
    except ValueError:
        # Provider returned plain text rather than the JSON schema.
        logger.debug("Fixer response was not structured JSON; cleaning raw text")
        return clean_sql(content)


async def afix_query(
    dialect: str,
    user_statement: str,
    incorrect_sql: str,
    error_message: str,
    table_schema: str,
    table_name: str,
) -> str:
    user_message = build_fix_user_message(
        user_statement=user_statement,
        incorrect_sql=incorrect_sql,
        error_message=error_message,
        table_schema=table_schema,
        table_name=table_name,
    )
    system_prompt = FIXER_SYSTEM_PROMPT_TEMPLATE.format(dialect=dialect)
    sql_query = await arun_fixer_completion(
        system_prompt=system_prompt, user_message=user_message
    )
    logger.debug("Query fixer response:", dialect=dialect, sql_query=sql_query)
    return sql_query


class LlmSqlFixer:
    """``SqlFixer`` implementation that delegates to the LLM fixer pipeline."""

    def __init__(self, dialect: str = "DuckDB", table_name: str = "the water tables") -> None:
        self._dialect = dialect
        self._table_name = table_name

    async def afix(
        self,
        incorrect_sql: str,
        error_message: str,
        user_statement: str,
        table_schema: str,
    ) -> str:
        """Invoke the LLM fixer to correct *incorrect_sql*."""
        return await afix_query(
            dialect=self._dialect,
            user_statement=user_statement,
            incorrect_sql=incorrect_sql,
            error_message=error_message,
            table_schema=table_schema,
            table_name=self._table_name,
        )
