"""One case, end to end: context → toolset → agent → runner → contract → result.

This is the rollout, and there is exactly one of it. **P8's ``predict_fn`` calls
this same function** (``agent_architecture.md`` §6): a search that built its
context, its toolset or its run config differently from the measurement pass
would differ on the one path that has to be identical, and the two numbers would
not be comparable. Everything a rollout varies — the candidate's instruction and
docstrings, the case's ``as_of``, the cache mode — is an argument here.

**What comes back is a fact about the run, never a score.** :class:`CaseResult`
carries the parsed contract, the trajectory of root-level tool calls, the
diagnostics, and the harness-exclusion verdict. Comparing any of it against a
case's ``expectations`` is ``harness/scoring.py``'s (T102).

**The exclusion rule is ``decisions.md`` § Tool errors and harness exclusion**,
implemented in :func:`scan_for_exclusions`: exclude what the candidate could not
have avoided and the harness cannot reproduce, score what the candidate
deterministically causes. So an ``upstream`` tool error marks the case
``harness_error`` and an ``invalid_argument`` does not — the second is a fumble
the candidate can be held to, and excluding it would let a candidate raise its
own average by failing hardest on the templates it handles worst.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import structlog
from google.adk.agents.invocation_context import LlmCallsLimitExceededError
from google.adk.events.event import Event
from google.adk.models.base_llm import BaseLlm
from google.adk.runners import Runner
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.genai import types

from harness.assertions import assert_inputs, assert_model_replays
from harness.contract import EVALUATION_ROOT_INSTRUCTION, AnswerContract, parse_contract
from water_assistant_agent.assistant.agents.root_agent.agent import (
    build_root_agent,
    rollout_run_config,
)
from water_assistant_agent.assistant.cache import ResponseCache
from water_assistant_agent.assistant.context import ScenarioContext
from water_assistant_agent.assistant.toolset import TEXT_TO_SQL_TOOL
from water_assistant_agent.assistant.tools.weather_client import make_weather_client

logger = structlog.get_logger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]

PINNED_DB = REPO_ROOT / "data" / "water.duckdb"
"""The pinned database every rollout reads, bounded per case by ``connect_asof``."""

EVAL_CACHE_DIR = REPO_ROOT / "eval" / "cache"
"""The committed response cache both live dependencies read through (§5).

One directory, two rules since T146: GR2L replays from it and a miss excludes the
case; weather is keyed per day, replays from it wherever it can and records the
days it cannot, in every pass mode.
"""

APP_NAME = "water-assistant-harness"

UPSTREAM = "upstream"
"""The ``error_type`` that excludes. ``invalid_argument`` is the one that does not."""

ExclusionSource = Literal["upstream", "text_to_sql_agent", "rollout"]


class ReplayCache(ResponseCache):
    """A response cache that refuses to call out, whatever its caller asks for.

    Replay is a property of the *pass*, not of each call site, and its clients do
    not agree on how to say so: ``ArchiveWeatherClient`` threads an ``allow_live``
    flag through to :meth:`ResponseCache.fetch_many`, while
    :func:`~..tools.gr2l_client.run_gr2l` passes only the cache and takes the
    default. Overriding the argument here makes "**this cache will not fill a
    miss live**" (T104) structural rather than a rule each client has to
    remember, in both entry points — a strictness that a method added later
    silently escaped would be no strictness at all.

    A miss is then a :class:`~..cache.CacheMissError`, which the tool wrappers
    convert to an ``upstream`` error — which is exactly what ``decisions.md``
    § Tool errors and harness exclusion says a replay cache miss should be: a
    harness error, excluded, not a wrong answer.

    **Since T146 this is GR2L's cache and not the pass's.** The weather half is
    bound to a recording :class:`ResponseCache` even on the measurement path
    (:func:`make_case_context`), so what this class now carries is the strict
    rule for the one component whose determinism the cache is load-bearing for.
    """

    refuses_live = True
    """Declared so :func:`~harness.assertions.assert_model_replays` can check it.

    A flag rather than an ``isinstance`` because the assertion is about the
    behaviour — this cache will not fill a miss live — and not about this class.
    """

    async def fetch(
        self,
        canonical_request: Any,
        live_fetch: Any,
        *,
        canary: Any = None,
        allow_live: bool = True,
    ) -> Any:
        return await super().fetch(
            canonical_request, live_fetch, canary=canary, allow_live=False
        )

    async def fetch_many(
        self,
        canonical_requests: Any,
        live_fill: Any,
        *,
        allow_live: bool = True,
    ) -> list[Any]:
        return await super().fetch_many(canonical_requests, live_fill, allow_live=False)


@dataclass(frozen=True)
class ToolCall:
    """One root-level tool call, as the model issued it.

    ``args`` is the argument object the trajectory scorer's ``argument_checks``
    walk (§6.1). There is no result here on purpose: a check addresses arguments
    only, which is what fixes the plotting family's scored surface to the
    agent-supplied half of the spec.
    """

    name: str
    args: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Exclusion:
    """Why one run was marked ``harness_error``, broken down for §7's per-arm count."""

    tool: str
    source: ExclusionSource
    details: str


@dataclass(frozen=True)
class CaseResult:
    """What one rollout produced. Compared against nothing — that is T102's.

    ``contract`` is ``None`` exactly when the final message parsed to neither
    status, which is the ``parse_failure`` diagnostic of §7 and never a wrong
    answer.
    """

    case_id: str
    template_id: str
    contract: AnswerContract | None
    final_text: str
    trajectory: tuple[ToolCall, ...]
    harness_error: bool
    exclusions: tuple[Exclusion, ...]
    diagnostics: Mapping[str, Any]

    @property
    def status(self) -> str | None:
        """The agent's contract status, or ``None`` on a parse failure."""
        return None if self.contract is None else self.contract.status

    @property
    def answer(self) -> bool | int | float | str | None:
        return None if self.contract is None else self.contract.answer

    @property
    def unit(self) -> str | None:
        return None if self.contract is None else self.contract.unit

    @property
    def tool_names(self) -> tuple[str, ...]:
        """The called tools in order, including repeats — the trajectory's key set."""
        return tuple(call.name for call in self.trajectory)


def make_case_context(
    as_of: datetime,
    *,
    db_path: Path | str = PINNED_DB,
    cache_dir: Path | str = EVAL_CACHE_DIR,
    allow_live: bool = False,
) -> ScenarioContext:
    """Build one case's context: a frozen clock over the pinned surfaces (§4).

    The clock is a closure returning *as_of* and is read fresh on every access,
    never captured — the same property production's advancing clock has, through
    the same code path.

    *allow_live* is the pass mode, and **it no longer binds both live
    dependencies alike** (T146). ``False`` is replay and is the default, because
    that is what a measurement pass runs under; what it binds is ``ctx.cache``,
    which is GR2L's — a :class:`ReplayCache`, so a window the pass did not
    capture is an ``upstream`` error and §7's exclusion channel still exists.
    ``True`` is the capture pass (T116), which records what it misses.

    **The weather half records in either mode**, and holds its own
    :class:`ResponseCache` over the same directory to say so. Its cache is keyed
    per day and carries no reproducibility claim (``agent_architecture.md`` §5),
    so a day the capture pass did not reach is worth one Archive request rather
    than a lost case: measured on the run this repaired, a weather miss cost the
    *case* and not the call — the agent retried with different arguments, missed
    again, and spent its step budget, so 160 of 336 holdout rollouts were
    excluded on windows nothing but the key's granularity made unreachable.
    """
    return ScenarioContext(
        clock=lambda: as_of,
        db_path=str(db_path),
        # The two dependencies are bound separately because their rules now
        # differ. `_model_cache` is the context's own — GR2L's, and deliberately
        # not the one handed to the weather client here.
        weather_client_factory=lambda db, _model_cache: make_weather_client(
            db, ResponseCache(cache_dir), allow_live=True
        ),
        http_cache=(ResponseCache if allow_live else ReplayCache)(cache_dir),
    )


async def run_case_async(
    inputs: Mapping[str, Any],
    *,
    instruction: str | None = None,
    docstrings: Mapping[str, str] | None = None,
    model: BaseLlm | None = None,
    db_path: Path | str = PINNED_DB,
    cache_dir: Path | str = EVAL_CACHE_DIR,
    allow_live: bool = False,
    roof_pool: str | None = None,
) -> CaseResult:
    """Run one case and report what happened.

    Args:
        inputs: The case's ``inputs`` object (§6.1) — ``question``, ``as_of``,
            ``case_id``, ``template_id``, ``params``. This is the whole of what a
            rollout is given; ``expectations`` never reaches this side.
        instruction: The candidate's root instruction. Defaults to the
            handwritten :data:`~harness.contract.EVALUATION_ROOT_INSTRUCTION`,
            which is what the pilot runs on.
        docstrings: The candidate's tool text, keyed by tool name.
        model: The task model. Defaults to the settings model, built per rollout.
        db_path: The pinned database.
        cache_dir: The committed response cache.
        allow_live: ``False`` (default) replays; ``True`` records a miss.
        roof_pool: The template's sampling pool, when it has one. Passed through
            to T104's preflight, which is the only thing that reads it — the
            pool is a property of the family and the envelope has no channel for
            it, so a caller that knows the template hands it over here.

    Raises:
        CaseAssertionError: the case broke one of T104's invariants, or the
            replay pass is bound to a cache that could call out. Raised rather
            than scored: a case that asks about days it cannot see is a testbed
            defect, and a number computed over it would be a number about
            nothing.

    Returns:
        A :class:`CaseResult`. Nothing in it is compared against the case's
        expectations — that is the scorers' job, and keeping the two apart is
        what lets one recorded run be re-scored without being re-run.

    The bindings are arguments rather than a pre-built context on purpose: the
    construction path is then the same one in a test, in the search and on the
    measurement run, and cannot silently diverge between them.
    """
    assert_inputs(inputs, roof_pool=roof_pool)
    as_of = _as_instant(inputs["as_of"])
    case_id = str(inputs.get("case_id", ""))
    question = inputs["question"]

    ctx = make_case_context(
        as_of, db_path=db_path, cache_dir=cache_dir, allow_live=allow_live
    )
    if not allow_live:
        assert_model_replays(ctx)
    agent = build_root_agent(
        ctx,
        instruction=EVALUATION_ROOT_INSTRUCTION if instruction is None else instruction,
        docstrings=docstrings,
        model=model,
    )

    session_service = InMemorySessionService()
    session = await session_service.create_session(
        app_name=APP_NAME, user_id="harness", session_id=case_id or None
    )
    runner = Runner(app_name=APP_NAME, agent=agent, session_service=session_service)

    events: list[Event] = []
    step_cap_exceeded = False
    rollout_failure: Exclusion | None = None
    started = time.monotonic()
    try:
        async for event in runner.run_async(
            user_id="harness",
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text=question)]),
            run_config=rollout_run_config(),
        ):
            events.append(event)
    except LlmCallsLimitExceededError:
        # Scored, not excluded. A candidate that spends the step cap without
        # answering has deterministically caused that, and `decisions.md`
        # § Tool errors and harness exclusion scores what the candidate causes.
        # It leaves no final message, so it lands as a `parse_failure` with the
        # cap named beside it — the diagnostic that tells the two apart.
        logger.warning("Rollout hit the tool-step cap", case_id=case_id)
        step_cap_exceeded = True
    except Exception as exc:  # noqa: BLE001 - the taxonomy's third source
        # The run itself failed: the model call, the transport, the runner. That
        # is an upstream failure the candidate could not have avoided, so it
        # excludes — and it is also where a defect in this harness would land,
        # which is why the per-arm exclusion counts of §7 are the alarm and are
        # published beside the results.
        logger.exception("Rollout failed", case_id=case_id)
        rollout_failure = Exclusion(
            tool="", source="rollout", details=f"{type(exc).__name__}: {exc}"
        )
    latency_s = time.monotonic() - started

    root_events = [event for event in events if event.author == agent.name]
    trajectory = _trajectory(root_events)
    exclusions = scan_for_exclusions(root_events)
    if rollout_failure is not None:
        exclusions = (*exclusions, rollout_failure)

    final_text = _final_text(root_events)
    contract = parse_contract(final_text)

    result = CaseResult(
        case_id=case_id,
        template_id=str(inputs.get("template_id", "")),
        contract=contract,
        final_text=final_text,
        trajectory=trajectory,
        harness_error=bool(exclusions),
        exclusions=exclusions,
        diagnostics={
            "steps": len(trajectory),
            "model_turns": sum(1 for event in events if event.usage_metadata),
            "tokens": _tokens(events),
            "latency_s": latency_s,
            "parse_failure": contract is None,
            "step_cap_exceeded": step_cap_exceeded,
            # The sub-agent's transpile/validate retries happen on the inner
            # `Runner` `AgentTool` builds, and nothing writes the count into the
            # state delta that reaches this side. Reported as unknown rather
            # than as zero, so T102 does not publish a fabricated diagnostic.
            "fixer_iterations": None,
        },
    )
    logger.info(
        "Case complete",
        case_id=case_id,
        status=result.status,
        tools=result.tool_names,
        harness_error=result.harness_error,
    )
    return result


def run_case(inputs: Mapping[str, Any], **kwargs: Any) -> CaseResult:
    """:func:`run_case_async` on its own event loop — one case, one call.

    A loop per rollout is what MLflow's threaded evaluation makes of this
    function, and until T148 it was a defect rather than a shape:
    ``gr2l_client`` kept its ``httpx.AsyncClient`` in a *process*-global
    singleton while httpx binds a connection pool to the loop that created it,
    so the second live GR2L call in a process failed with ``Event loop is
    closed`` — arriving as an ``upstream`` error through
    :class:`~..cache.CacheMissError` and so indistinguishable from an
    unreachable service. Recorded under T115 as a capture-pass hazard on the
    ground that "replay never reaches the client"; that scoping was right about
    the measurement path and wrong about the **search**, which records
    (``allow_live=True``) and reaches the client on any window the capture pass
    did not hold. The client now keys its pool per loop, so a loop per rollout
    is merely a loop per rollout.
    """
    return asyncio.run(run_case_async(inputs, **kwargs))


def scan_for_exclusions(root_events: Iterable[Event]) -> tuple[Exclusion, ...]:
    """The harness-exclusion scan over one run's **root-level** tool results.

    Two populations, and they are found two different ways because the tools
    report failure two different ways.

    * Every tool wrapper in ``assistant/tools/`` returns a typed
      :class:`~..tools.schemas.ErrorResult`, so an ``upstream`` failure is found
      by field: ``error_type == "upstream"``. An ``invalid_argument`` is left
      alone and the case stays scored.
    * ``text_to_sql_agent`` is a frozen sub-agent whose payload carries no
      ``error_type`` at all — its contract is ``{"status": "error",
      "error_details": …}`` — so it is found **keyed on the tool name**: a result
      that parses as a dict with an error status excludes. The split is
      unavailable there, so the whole population is treated as upstream.

    Root-level only. The sub-agent's inner fixer loop runs on its own ``Runner``
    and its events never enter this stream; a retried-and-repaired query is not a
    failure of the run, and scanning for it would exclude cases the candidate
    handled correctly.
    """
    exclusions: list[Exclusion] = []
    for name, payload in _tool_results(root_events):
        if isinstance(payload, Mapping) and payload.get("error_type") == UPSTREAM:
            exclusions.append(
                Exclusion(
                    tool=name,
                    source="upstream",
                    details=str(payload.get("error_details", "")),
                )
            )
            continue
        if name != TEXT_TO_SQL_TOOL:
            continue
        parsed = payload if isinstance(payload, Mapping) else _as_mapping(payload)
        if parsed is not None and parsed.get("status") == "error":
            exclusions.append(
                Exclusion(
                    tool=name,
                    source="text_to_sql_agent",
                    details=str(parsed.get("error_details", "")),
                )
            )
    return tuple(exclusions)


def _tool_results(root_events: Iterable[Event]) -> list[tuple[str, Any]]:
    """``(tool name, payload)`` per root-level function response, in order.

    ADK wraps a non-dict tool return as ``{"result": value}`` before it becomes a
    ``FunctionResponse`` (``flows/llm_flows/functions.py``), and the sub-agent's
    tool returns a bare string whenever the model answered in prose. Unwrapping
    that one key here means the scan sees what the tool returned rather than what
    ADK boxed it in; no tool of this toolset returns a lone ``result`` key of its
    own, so the unwrap is unambiguous.
    """
    results: list[tuple[str, Any]] = []
    for event in root_events:
        for response in event.get_function_responses():
            payload: Any = response.response
            if isinstance(payload, Mapping) and set(payload) == {"result"}:
                payload = payload["result"]
            results.append((response.name or "", payload))
    return results


def _trajectory(root_events: Iterable[Event]) -> tuple[ToolCall, ...]:
    """The root agent's tool calls in the order it issued them, repeats included."""
    return tuple(
        ToolCall(name=call.name or "", args=dict(call.args or {}))
        for event in root_events
        for call in event.get_function_calls()
    )


def _final_text(root_events: Iterable[Event]) -> str:
    """The run's last complete text turn, or ``""`` if it produced none.

    ``""`` is what a step-cap exhaustion leaves behind, and
    :func:`~harness.contract.parse_contract` reads it as a parse failure — which
    is the honest classification: the candidate delivered no contract.

    **A reasoning model's scratchpad is not its answer** (T134). ADK marks a
    reasoning part ``thought=True`` and leaves the reply beside it as an ordinary
    text part, so joining every part prepends the chain of thought to the message
    the contract is read from. The parser survives that — it scans backwards for
    the last embedded object — but what it survives is a message no candidate
    wrote, and the scored ``explanation`` would be a trace that is not stable at
    temperature 0. Thought parts are dropped for the same reason
    ``harness/reflection.py`` keeps them out of the reflection canary.

    It is a no-op on a model that emits none, which is every model measured
    before T134: gemma-4-31b-it produced no thought part, so this changes nothing
    about the P8c run's numbers and only decides what a reasoning model's final
    message is.
    """
    for event in reversed(list(root_events)):
        if event.partial or event.get_function_calls() or event.get_function_responses():
            continue
        content = event.content
        if content is None or not content.parts:
            continue
        text = "".join(part.text or "" for part in content.parts if not part.thought)
        if text.strip():
            return text
    return ""


def _tokens(events: Iterable[Event]) -> dict[str, int]:
    """Prompt, candidate and total token counts summed over the run's model turns."""
    totals = {"prompt": 0, "candidates": 0, "total": 0}
    for event in events:
        usage = event.usage_metadata
        if usage is None:
            continue
        totals["prompt"] += usage.prompt_token_count or 0
        totals["candidates"] += usage.candidates_token_count or 0
        totals["total"] += usage.total_token_count or 0
    return totals


def _as_mapping(payload: Any) -> Mapping[str, Any] | None:
    """*payload* as a mapping, parsing it as JSON text if that is what it is."""
    if isinstance(payload, Mapping):
        return payload
    if not isinstance(payload, str):
        return None
    try:
        value = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _as_instant(as_of: datetime | str) -> datetime:
    """A case's ``as_of`` as an instant, offset and all.

    The stamp is written in the site's own offset-bearing form and stays that
    way here: the conversion to UTC belongs to ``connect_asof``, never to a
    caller (``decisions.md`` § The as-of cut).
    """
    if isinstance(as_of, datetime):
        return as_of
    instant = datetime.fromisoformat(as_of)
    if instant.tzinfo is None:
        raise ValueError(
            f"A case's as_of must carry the site's own UTC offset, got {as_of!r}."
        )
    return instant
