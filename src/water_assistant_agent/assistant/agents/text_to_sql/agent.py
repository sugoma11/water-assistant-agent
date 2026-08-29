"""Text-to-SQL sub-agent: NL -> DuckDB SQL -> validated execution.

Wires the builder (NL -> SQL), the transpile/validate pipeline, and the
query-execution tool into a single ADK ``Agent``, mirroring core-agent's
``analytic_table_agent/agent.py``.

**Frozen means the text, not the objects** (``agent_architecture.md`` §3.1). The
chain executor → validator → pipeline → tools → ``Agent`` → ``AgentTool`` is
frozen at import, so an injectable executor at the bottom makes nothing above it
reconfigurable: the agent is *rebuilt* per rollout by
:func:`build_text_to_sql_agent`, which binds both DB seams — the inner query tool
and the pipeline's EXPLAIN validator — to that case's as-of executor. Everything
that is text (name, description, instruction, both tool docstrings) and every
stateless object (transpiler, fixers) is shared across builds, so two contexts'
agents differ in their binding and in nothing else. The module-level
``text_to_sql_agent`` is the production default, from the same factory.
"""

from collections.abc import Callable
from typing import Any

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
from water_assistant_agent.assistant.context import Clock
from water_assistant_agent.assistant.ports import ReadOnlyWarehouseQuery
from water_assistant_agent.assistant.settings import get_settings
from water_assistant_agent.assistant.tools.site import site_now
from water_assistant_agent.assistant.tools.warehouse import (
    SETTINGS_EXECUTOR,
    make_query_database_tool,
)
from water_assistant_agent.tenants.green_roof.sensordata import table_schema_dict

logger = structlog.get_logger(__name__)

QueryBuilderTool = Callable[[str], Any]
"""What :func:`make_query_builder_tool` returns: the ADK-facing builder tool."""

# --- The frozen text. Byte-stable across every build; only the binding varies. --

AGENT_NAME = "text_to_sql_agent"
"""Also the ``AgentTool`` name the root agent sees, which trajectory scoring keys
on (``findings.md`` § Codebase seams)."""

AGENT_DESCRIPTION = (
    "Water-Management Data Analyst for green-roof sensor data "
    "(outflow, radiation, soil moisture, soil temperature, weather).\n\n"
    "This text is the declaration of ``text_to_sql_agent``, one of the tools the "
    "assistant may call."
)
"""The sub-agent's outward description — an optimizable candidate component
(T120), which is why :func:`build_text_to_sql_agent` takes it as an argument.

Its second sentence is the one every tool text carries (T136): this is the sixth
tool's declared text, and ``AgentTool`` presents it where the five function tools
present a ``__doc__``, so it says what it is in the same words they do."""

_RESPONSE_SHAPE_TEXT = """On success: {"status": "success", "sql": <string>, "reasoning": <string>}
On failure: {"status": "error", "error_details": <string>}"""

STATIC_INSTRUCTION = build_agent_instruction(
    builder_tool="query_builder_tool",
    database_tool="query_database_tool",
    response_schema_text=_RESPONSE_SHAPE_TEXT,
)
"""Built once and reused, so every agent this module builds carries the identical
instruction object — the frozen text cannot drift between two rollouts."""

# --- The shared, stateless objects. Configuration only; no per-case state. -----

_TRANSPILER = DuckDbSqlTranspiler(sqlglot_schema=build_sqlglot_schema(table_schema_dict))
_TRANSPILE_FIXER = LlmSqlFixer()
_VALIDATE_FIXER = LlmSqlFixer()


def make_query_builder_tool(pipeline: TextToSqlPipeline) -> QueryBuilderTool:
    """Build the NL→SQL tool over *pipeline*.

    The pipeline carries the case's validator, so this tool is rebuilt per
    context alongside it. Like the query tool, the closure keeps the exact name,
    signature and docstring: ADK derives the declaration from the function and
    the frozen instruction names ``query_builder_tool``.
    """

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
            pipeline_result = await pipeline.aexecute(
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

    query_builder_tool.__qualname__ = "query_builder_tool"
    return query_builder_tool


def _build_model() -> LiteLlm:
    settings = get_settings()
    return LiteLlm(
        model=settings.text_to_sql_agent_model,
        **settings.litellm_extra(settings.text_to_sql_agent_model),
    )


def build_text_to_sql_agent(
    executor: ReadOnlyWarehouseQuery,
    clock: Clock,
    description: str | None = None,
) -> Agent:
    """Build the frozen sub-agent bound to one executor and one clock.

    Both DB seams are bound here and nowhere else: the pipeline's EXPLAIN
    validator and the inner query tool receive the same *executor*, so the SQL
    the pipeline approves and the SQL that runs meet the identical catalog. The
    only other per-case state is *clock*, read by the querier's SQL rewrite (§5).

    *description* is the sub-agent's outward text, threaded from the candidate's
    docstrings by T029; it defaults to the frozen production wording. Everything
    else — name, instruction, both tool docstrings, transpiler, fixers, model id
    — is shared and byte-stable, which is what §3.1's "frozen" means.
    """
    pipeline = TextToSqlPipeline(
        transpiler=_TRANSPILER,
        validator=DuckDbExplainValidator(executor),
        transpile_fixer=_TRANSPILE_FIXER,
        validate_fixer=_VALIDATE_FIXER,
        max_retries=get_settings().max_sql_retries,
    )
    return Agent(
        model=_build_model(),
        name=AGENT_NAME,
        description=AGENT_DESCRIPTION if description is None else description,
        static_instruction=STATIC_INSTRUCTION,
        tools=[
            make_query_builder_tool(pipeline),
            make_query_database_tool(executor, clock),
        ],
    )


text_to_sql_agent = build_text_to_sql_agent(SETTINGS_EXECUTOR, site_now)
"""The production default — the unbounded settings executor and the site clock.

Built by the factory the harness calls, so ``bootstrap.py`` and the chat service
keep importing one name while every rollout gets its own bound agent.
"""
