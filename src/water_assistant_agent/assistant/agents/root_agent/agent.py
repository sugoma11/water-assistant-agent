"""Root orchestrator agent: routes user requests to the text-to-SQL sub-agent.

Single-tenant water-management assistant, so unlike core-agent's
``root/factory.py`` (which builds a per-tenant agent with dynamically wired
tools) there is exactly one tenant and one data agent.

The agent is built by :func:`build_root_agent` — one per rollout for the harness,
once at import for the service — and everything a candidate can move is an
argument to it: the instruction, the tool docstrings, and through them the tools
themselves (``agent_architecture.md`` §2). The scenario clock reaches the model
the same way in both cases, through a per-invocation instruction provider closed
over ``ctx.clock``: production's advances, a case's is frozen at its ``as_of``,
and there is one code path.
"""

from collections.abc import Mapping
from typing import Any

from google.adk.agents.llm_agent import Agent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.agents.run_config import RunConfig
from google.adk.models.base_llm import BaseLlm
from google.adk.models.lite_llm import LiteLlm

from water_assistant_agent.assistant.context import ScenarioContext
from water_assistant_agent.assistant.prompts.temporal import current_datetime_block
from water_assistant_agent.assistant.settings import get_settings
from water_assistant_agent.assistant.toolset import build_toolset, production_context

ROOT_INSTRUCTION = """You are a helpful assistant for a water-management research team studying green-roof sensor data (outflow, radiation, soil moisture, soil temperature, weather).

### Your responsibilities

1. **Identify request type**
   Determine whether the user's message is a follow-up to the current problem or a new problem statement. If unsure, ask exactly:
   > "Is this a new problem statement you want me to solve, or a change to the current issue we are working on?"

2. **Delegate correctly**
   * If the request requires SQL generation, database queries, or data-based reasoning over the measured sensor data, delegate it to the **text_to_sql_agent** agent.
   * Do not ask or pass column names by yourself. You may ask about specific columns *ONLY* if the user asked to. text_to_sql_agent is smart enough to decide what columns to use.
   * If the request needs *modelled* green-roof hydrology over a period — stormwater retention, roof runoff, soil moisture / drought risk, or evapotranspiration — for a given roof type, call **predict_green_roof_water_balance_tool**. It fetches both the weather and the roof's starting soil moisture itself; you only supply the roof type and the date window. Do not call the weather tool or query the database first to feed it. Use **get_weather_forecast_tool** when the user just wants daily weather.
   * The model covers three roof segments: non-irrigated extensive, irrigated extensive, semi-intensive. The **gravel roof and the wetland cannot be modelled** — the gravel roof has no substrate, and the wetland's soil-moisture sensor cannot measure the water ponded above its mat. Their *measured* data (soil moisture, outflow, temperature) is still available through text_to_sql_agent, so a question about what either roof actually did is a normal database question. Still pass the roof the user asked about to the tool: it reports the scope limit itself, with the reason to give them.
   * If the request is about what the site's **documented rules, thresholds, definitions or reference values** are — the irrigation rule and its trigger levels, substrate properties, doses, what counts as a heatwave, the retention target, what a soil-moisture reading means, the roof segments, the instruments, how ET0 is computed, or how far each table's record runs — call **lookup_reference** and name the closest `topic`. It reads the site's own reference cards, so a documented constant comes from there and never from your own knowledge or from a database query. Read the card whole, including its `not_applicable` block: a segment listed there has no such value at all, and a condition the card does not state is one the site does not have — say so plainly rather than estimating from the segments that do.
   * Soil moisture is reported in **% water content**, with millimetres of stored water alongside. Report the unit the tool gives you and never convert between the two yourself.
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


AGENT_NAME = "root_agent"
AGENT_DESCRIPTION = "Water-Management Data Analyst."

MAX_TOOL_STEPS = 6
"""The ReAct loop's hard cap on tool steps for one rollout (§2)."""

MAX_LLM_CALLS = MAX_TOOL_STEPS + 1
"""The cap as ADK enforces it: LLM calls, not tool steps.

There is no tool-step field on an ``Agent``; ADK counts **model turns** per
invocation, in ``RunConfig.max_llm_calls``. In a ReAct loop each tool step costs
one model turn — the turn that emits the call — and the answer that follows the
last tool result costs one more, so ``MAX_TOOL_STEPS`` steps is
``MAX_TOOL_STEPS + 1`` calls. ADK increments the counter and then raises when it
*exceeds* the limit, so exactly this many calls are allowed.

Two things it does not count. The sub-agent's own turns run on a separate
``Runner`` that ``AgentTool`` builds with a default ``RunConfig``
(``agent_tool.py``), so the text-to-SQL agent's builder/query loop is bounded by
its own 500 and not by this; one ``text_to_sql_agent`` call is one step here
however many turns it takes inside. And a retried model call after a transport
error counts as a call, so the bound is on calls issued, not on distinct steps
taken.
"""


def _build_model() -> LiteLlm:
    settings = get_settings()
    return LiteLlm(model=settings.root_agent_model, **settings.litellm_extra())


def _make_temporal_instruction(ctx: ScenarioContext):
    """Build the per-invocation date block provider for *ctx* (ADK ``InstructionProvider``).

    ``static_instruction`` is sent to the model verbatim and is built once when
    the agent is, so the date cannot live there — a long-running server would
    keep reporting the day it booted, and a case would report the day its
    rollout ran. ADK re-resolves ``instruction`` on every invocation instead, and
    this closure reads ``ctx.clock()`` at that moment: production's site clock
    advances, a case's returns its frozen ``as_of``, and neither is captured
    here. Because ``static_instruction`` is set, ADK places this block at the
    front of the request contents rather than in the system instruction, which
    keeps the large static prefix byte-stable for prompt caching.
    """

    def temporal_instruction(_readonly_ctx: ReadonlyContext) -> str:
        return current_datetime_block(now=ctx.clock())

    return temporal_instruction


def build_root_agent(
    ctx: ScenarioContext,
    instruction: str | None = None,
    docstrings: Mapping[str, str] | None = None,
    tools: list[Any] | None = None,
    model: BaseLlm | None = None,
) -> Agent:
    """Build a root agent bound to *ctx*, carrying one candidate's text.

    Args:
        ctx: The rollout's context. Its clock reaches the model through the
            instruction provider; its executor reaches the tools through
            :func:`build_toolset`.
        instruction: The candidate's root instruction, sent as
            ``static_instruction``. Defaults to the production
            :data:`ROOT_INSTRUCTION`.
        docstrings: The candidate's tool text, keyed by tool name; passed to
            :func:`build_toolset`. Mutually exclusive with *tools*.
        tools: An already-built toolset, for a caller that has one. Defaults to
            ``build_toolset(ctx, docstrings)``.
        model: The task model. Defaults to a fresh :class:`LiteLlm` on the
            settings model id — per build, since two rollouts should not share a
            client, though the id and decoding parameters that pin it are the
            same (§3.1's sense of "shared").

    Raises:
        ValueError: both *docstrings* and *tools* were given, which would silently
            drop the docstrings — a component frozen without anyone noticing is
            the failure ``decisions.md`` § The optimizer entry point and the
            candidate surface names.
    """
    if docstrings is not None and tools is not None:
        raise ValueError(
            "Pass either docstrings or a pre-built toolset, not both: the "
            "docstrings would be ignored and the candidate silently truncated."
        )
    return Agent(
        model=_build_model() if model is None else model,
        name=AGENT_NAME,
        description=AGENT_DESCRIPTION,
        static_instruction=ROOT_INSTRUCTION if instruction is None else instruction,
        instruction=_make_temporal_instruction(ctx),
        tools=build_toolset(ctx, docstrings) if tools is None else tools,
    )


def rollout_run_config() -> RunConfig:
    """The ``RunConfig`` every rollout runs under: §2's tool-step cap.

    The cap lives on the run, not on the agent, so :func:`build_root_agent`
    cannot carry it: whoever drives the ``Runner`` must pass this. **P6's
    ``run_case`` and P8's ``predict_fn`` both call this one function** — a search
    that bounded its candidates differently from the measurement path would
    differ on the one thing that decides whether a shotgun candidate finishes at
    all.

    Exceeding it raises :class:`~google.adk.agents.invocation_context.LlmCallsLimitExceededError`
    mid-run; ADK offers no truncate-and-answer mode. Classifying that outcome —
    a wrong answer, an abstention, or a harness exclusion — is P6a's, with the
    rest of the error taxonomy (``decisions.md`` § Tool errors and harness
    exclusion), not this task's.

    The production chat path deliberately keeps ADK's default: ``ag_ui_adk``
    takes a ``run_config_factory`` and this could be wired into ``bootstrap.py``,
    but the cap converts a runaway loop into an exception rather than an answer,
    and turning a live user's long conversation into a 500 is not what "the prose
    answer path is unchanged" (T036) means.
    """
    return RunConfig(max_llm_calls=MAX_LLM_CALLS)


root_agent = build_root_agent(production_context())
"""The production default — the site clock and the unbounded settings executor.

Built by the factory the harness calls, so ``bootstrap.py`` and the frontend keep
importing one name while every rollout gets its own bound agent.
"""
