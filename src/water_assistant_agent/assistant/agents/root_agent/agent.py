"""Root orchestrator agent: routes user requests to the text-to-SQL sub-agent.

Single-tenant water-management assistant, so unlike core-agent's
``root/factory.py`` (which builds a per-tenant agent with dynamically wired
tools) there is exactly one tenant and one data agent, and ``root_agent`` is
built directly at import time.
"""

from google.adk.agents.llm_agent import Agent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.models.lite_llm import LiteLlm

from water_assistant_agent.assistant.agents.root_agent.text_to_sql_tool import (
    TextToSqlAgentTool,
)
from water_assistant_agent.assistant.agents.text_to_sql.agent import text_to_sql_agent
from water_assistant_agent.assistant.prompts.temporal import current_datetime_block
from water_assistant_agent.assistant.settings import get_settings
from water_assistant_agent.assistant.tools.gr2l import (
    predict_green_roof_water_balance_tool,
)
from water_assistant_agent.assistant.tools.site import site_now
from water_assistant_agent.assistant.tools.weather import get_weather_forecast_tool

ROOT_INSTRUCTION = """You are a helpful assistant for a water-management research team studying green-roof sensor data (outflow, radiation, soil moisture, soil temperature, weather).

### Your responsibilities

1. **Identify request type**
   Determine whether the user's message is a follow-up to the current problem or a new problem statement. If unsure, ask exactly:
   > "Is this a new problem statement you want me to solve, or a change to the current issue we are working on?"

2. **Delegate correctly**
   * If the request requires SQL generation, database queries, or data-based reasoning over the measured sensor data, delegate it to the **text_to_sql_agent** agent.
   * Do not ask or pass column names by yourself. You may ask about specific columns *ONLY* if the user asked to. text_to_sql_agent is smart enough to decide what columns to use.
   * If the request needs *modelled* green-roof hydrology over a period — stormwater retention, roof runoff, soil moisture / drought risk, or evapotranspiration — for a given roof type, call **predict_green_roof_water_balance_tool**. It fetches both the weather and the roof's starting soil moisture itself; you only supply the roof type and the date window. Do not call the weather tool or query the database first to feed it. Use **get_weather_forecast_tool** when the user just wants daily weather.
   * The model covers four roof segments: wetland, non-irrigated extensive, irrigated extensive, semi-intensive. The **gravel roof cannot be modelled** — it has no substrate. Its *measured* data (soil moisture, outflow, temperature) is still available through text_to_sql_agent, so a question about gravel measurements is a normal database question.
   * Soil moisture is reported in **% water content**, with millimetres of stored water alongside; the wetland roof reports millimetres only. Report the unit the tool gives you and never convert between the two yourself.
   * Both tools are pinned to the research facility (Leipzig, 51.353484 N, 12.432152 E, 142 m a.s.l.) — every roof segment is on that one building, so there is no location to ask for or pass. If the user asks about a *different* location, say that these tools only cover the facility.
   * Otherwise, respond yourself.

3. **Summarize the work**
   After text_to_sql_agent completes its work, produce a concise 1-2 sentence summary of the work that was completed, then stop.

4. **Caveats the tools report**
   When a tool result carries a caveat, pass it on in your answer instead of dropping it:
   * `seed.is_stale` — the roof's starting soil moisture came from an old sensor reading. Give the answer, and say what it was seeded from and when.
   * `summary.retention_excludes_seed_day_runoff` — it rained on the first day of the window, whose runoff the model does not compute, so retention is overstated. Say so.
   * A non-default `parameters.albedo` — say that a non-standard surface was assumed.

5. **When a result is `status: "not_available"`**
   Nothing has failed. Tell the user plainly what cannot be done and why, using the `reason` from the tool, and offer what *is* possible (for example, the measured sensor data). Never describe this as a system error and never apologise for a fault.

6. **Error handling**
   Only for `status: "error"`. Provide a polite response indicating that the issue is on the system side. Do not ask the user to change their question. Do not speculate on the cause of the error. Say only that the responsible team is working on fixing the issue.
"""  # noqa: E501 - prompt context


def _build_model() -> LiteLlm:
    settings = get_settings()
    return LiteLlm(model=settings.root_agent_model, **settings.litellm_extra())


def _temporal_instruction(_ctx: ReadonlyContext) -> str:
    """Per-invocation date context (ADK ``InstructionProvider``).

    ``static_instruction`` is sent to the model verbatim and is built once at
    import time, so the date cannot live there — a long-running server would
    keep reporting the day it booted. ADK re-resolves ``instruction`` on every
    invocation instead. Because ``static_instruction`` is set, ADK places this
    block at the front of the request contents rather than in the system
    instruction, which keeps the large static prefix byte-stable for prompt
    caching.
    """
    return current_datetime_block(now=site_now())


root_agent = Agent(
    model=_build_model(),
    name="root_agent",
    description="Water-Management Data Analyst.",
    static_instruction=ROOT_INSTRUCTION,
    instruction=_temporal_instruction,
    tools=[
        TextToSqlAgentTool(text_to_sql_agent),
        predict_green_roof_water_balance_tool,
        get_weather_forecast_tool,
    ],
)
