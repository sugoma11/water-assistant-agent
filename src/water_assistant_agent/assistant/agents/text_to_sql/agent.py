"""Text-to-SQL sub-agent: NL -> DuckDB SQL -> validated execution.

Wires the builder (NL -> SQL), the transpile/validate pipeline, and the
query-execution tool into a single ADK ``Agent``, mirroring core-agent's
``analytic_table_agent/agent.py``.
"""

import structlog
from google.adk.agents.llm_agent import Agent
from google.adk.models.lite_llm import LiteLlm

from water_assistant_agent.assistant.agents.text_to_sql.builder import (
    abuild_sql,
    formatted_schema,
)
from water_assistant_agent.assistant.agents.text_to_sql.dry_run import (
    DuckDbExplainValidator,
)
from water_assistant_agent.assistant.agents.text_to_sql.fixers import LlmSqlFixer
from water_assistant_agent.assistant.agents.text_to_sql.pipeline import (
    TextToSqlPipeline,
)
from water_assistant_agent.assistant.agents.text_to_sql.transpiler import (
    DuckDbSqlTranspiler,
    build_sqlglot_schema,
)
from water_assistant_agent.assistant.prompts.agent_instructions import (
    build_agent_instruction,
)
from water_assistant_agent.assistant.settings import get_settings
from water_assistant_agent.assistant.tools.warehouse import query_database_tool
from water_assistant_agent.tenants.green_roof.sensordata import table_schema_dict

logger = structlog.get_logger(__name__)

_RESPONSE_SHAPE_TEXT = """On success: {"status": "success", "sql": <string>, "reasoning": <string>}
On failure: {"status": "error", "error_details": <string>}"""

_PIPELINE = TextToSqlPipeline(
    transpiler=DuckDbSqlTranspiler(sqlglot_schema=build_sqlglot_schema(table_schema_dict)),
    validator=DuckDbExplainValidator(),
    transpile_fixer=LlmSqlFixer(),
    validate_fixer=LlmSqlFixer(),
    max_retries=get_settings().max_sql_retries,
)


async def query_builder_tool(user_statement: str) -> dict[str, str]:
    """Build and validate a DuckDB SQL query for the water database from natural language.

    Args:
        user_statement: The user's natural-language question about the
            green-roof sensor data (outflow, radiation, soil moisture,
            soil temperature, weather).

    Returns:
        dict with ``status``, ``sql``, and ``reasoning`` on success,
        or ``status`` and ``error_details`` on failure.
    """
    try:
        builder_result = await abuild_sql(user_statement)
        pipeline_result = await _PIPELINE.aexecute(
            postgresql_sql=builder_result.sql,
            user_statement=user_statement,
            table_schema=formatted_schema(),
        )
    except Exception:
        logger.exception("Error in text-to-SQL query builder")
        return {
            "status": "error",
            "error_details": "Error building the SQL query. Please try rephrasing.",
        }
    result = {
        "status": "success",
        "sql": pipeline_result.final_sql,
        "reasoning": builder_result.reasoning,
    }
    logger.debug("Text-to-SQL query builder result", result=result)
    return result


def _build_model() -> LiteLlm:
    settings = get_settings()
    return LiteLlm(model=settings.text_to_sql_agent_model, **settings.litellm_extra())


text_to_sql_agent = Agent(
    model=_build_model(),
    name="text_to_sql_agent",
    description=(
        "Water-Management Data Analyst for green-roof sensor data "
        "(outflow, radiation, soil moisture, soil temperature, weather)."
    ),
    static_instruction=build_agent_instruction(
        builder_tool="query_builder_tool",
        database_tool="query_database_tool",
        response_schema_text=_RESPONSE_SHAPE_TEXT,
    ),
    tools=[query_builder_tool, query_database_tool],
)
