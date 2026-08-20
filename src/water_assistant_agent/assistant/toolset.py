"""``build_toolset`` — the root agent's tools, built once per ``ScenarioContext``.

Every tool the root agent can call is produced here from a factory that closes
over one context, so two rollouts running side by side share no executor, no
clock and no tool object (``agent_architecture.md`` §4). Production is not a
second path: it builds a context around the lazy settings executor and the site
clock (:func:`production_context`) and calls the same function.

**Docstrings are candidate text.** ADK derives each tool declaration from the
callable — its name, signature and ``__doc__`` — so the docstring is what the
root model actually reads when deciding which tool to call, and §2 lists it among
the optimizable components. *docstrings* maps a tool name to the candidate's
text; the sub-agent's entry is its outward ``description`` (the text ``AgentTool``
presents) rather than a Python docstring, and is threaded into
:func:`build_text_to_sql_agent`. A name that is not a tool raises rather than
being ignored: a silently dropped component is scored as if it had been applied,
which is the failure ``decisions.md`` § The optimizer entry point and the
candidate surface exists to prevent.

Three tools today. The card, irrigation and plot tools join this list in P3–P5,
and the names below are what the catalog's trajectory expectations key on.
"""

from collections.abc import Mapping
from typing import Any

import structlog

from water_assistant_agent.assistant.agents.root_agent.text_to_sql_tool import (
    TextToSqlAgentTool,
)
from water_assistant_agent.assistant.agents.text_to_sql.agent import (
    build_text_to_sql_agent,
)
from water_assistant_agent.assistant.context import ScenarioContext
from water_assistant_agent.assistant.tools.gr2l import make_green_roof_balance_tool
from water_assistant_agent.assistant.tools.site import site_now
from water_assistant_agent.assistant.tools.warehouse import SETTINGS_EXECUTOR
from water_assistant_agent.assistant.tools.weather import make_weather_forecast_tool

logger = structlog.get_logger(__name__)

TEXT_TO_SQL_TOOL = "text_to_sql_agent"
GREEN_ROOF_TOOL = "predict_green_roof_water_balance_tool"
WEATHER_TOOL = "get_weather_forecast_tool"

TOOL_NAMES: tuple[str, ...] = (TEXT_TO_SQL_TOOL, GREEN_ROOF_TOOL, WEATHER_TOOL)
"""The addressable tools, in the order the root agent declares them.

Declaration order is part of the prompt the model sees, so it is fixed here
rather than left to the caller, and it matches the order the service has always
sent.
"""


def build_toolset(
    ctx: ScenarioContext,
    docstrings: Mapping[str, str] | None = None,
) -> list[Any]:
    """Build this context's tools, applying *docstrings* to the produced callables.

    Args:
        ctx: The rollout's context. Its clock and its executor are what the tools
            close over; nothing is read from a module-level singleton.
        docstrings: Optional candidate text keyed by tool name (:data:`TOOL_NAMES`).
            Omitted names keep the production wording, so a partial candidate is a
            partial override rather than an empty prompt.

    Returns:
        The tool list, in :data:`TOOL_NAMES` order: the sub-agent wrapped in
        :class:`TextToSqlAgentTool`, then the two function tools.

    Raises:
        ValueError: *docstrings* names something that is not a tool.
    """
    texts = dict(docstrings or {})
    unknown = sorted(set(texts) - set(TOOL_NAMES))
    if unknown:
        raise ValueError(
            f"Unknown tool name(s) in docstrings: {', '.join(unknown)}. "
            f"Known tools: {', '.join(TOOL_NAMES)}."
        )

    # The sub-agent's optimizable text is its outward description, not a
    # docstring: `AgentTool` presents `description` to the root model, and the
    # agent's own instruction is frozen (§3.1).
    sub_agent = build_text_to_sql_agent(
        ctx.db, ctx.clock, description=texts.get(TEXT_TO_SQL_TOOL)
    )
    green_roof_tool = make_green_roof_balance_tool(ctx)
    weather_tool = make_weather_forecast_tool(ctx)

    for name, tool in ((GREEN_ROOF_TOOL, green_roof_tool), (WEATHER_TOOL, weather_tool)):
        text = texts.get(name)
        if text is not None:
            # Safe because the callable is this context's own closure, freshly
            # built above — never a shared module-level function.
            tool.__doc__ = text

    logger.debug("Built toolset", tools=TOOL_NAMES, overridden=sorted(texts))
    return [TextToSqlAgentTool(sub_agent), green_roof_tool, weather_tool]


class _ProductionContextHolder:
    """Module-level holder for the production context (built on first use)."""

    instance: ScenarioContext | None = None


def production_context() -> ScenarioContext:
    """The context the running service binds its tools to.

    Two differences from a case's context, and only two. The clock is
    :func:`site_now`, so "now" advances; and the executor is
    :data:`SETTINGS_EXECUTOR`, the whole pinned file with no ``as_of`` bound and
    no connection opened until the first query — production has no case to be
    bounded by, and importing the service must not touch DuckDB.

    ``weather`` is ``None`` on purpose: no tool reads ``ctx.weather`` yet (both
    wrappers still call ``fetch_daily_weather`` directly), and a placeholder
    client would be the wrong one — the only implementation today is
    Archive-only. T043's composite fills it, and until then a premature consumer
    fails loudly instead of silently reading the wrong source.
    """
    if _ProductionContextHolder.instance is None:
        _ProductionContextHolder.instance = ScenarioContext.bound(
            clock=site_now,
            db=SETTINGS_EXECUTOR,
            weather=None,
            cache=None,
        )
    return _ProductionContextHolder.instance
