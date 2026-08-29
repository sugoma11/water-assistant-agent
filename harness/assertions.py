"""The invariants a case is held to, checked rather than trusted (T104).

Four properties, and none of them is a scoring rule. They are the conditions
under which a case's answer means anything at all — a case that violates one is a
**testbed defect**, so the harness raises rather than scoring the run, and the
generator (T111) evaluates the same predicates before it ever emits the case.

* **A retrospective comparison window ends at or before ``as_of``.** The measured
  record does not exist past the cut, so a comparison that reaches beyond it is
  comparing a prediction against nothing and would report a deviation computed
  over whatever days happened to overlap.
* **The model replays.** Structural rather than observed: a replay context is
  bound to a cache that refuses to fetch GR2L live, so a miss fails loudly as an
  ``upstream`` error instead of quietly running whatever build the service is
  serving today. It covers GR2L and no longer the weather half, which since T146
  fills and records a day the capture pass did not reach — the cache is
  load-bearing for the model's determinism and for weather's cost alone
  (``agent_architecture.md`` §5).
* **The roof pool is respected per family.** A template reading an outflow column
  cannot sample the semi-intensive roof, which has none; a modelling template
  cannot sample the two roofs the water balance declines. Both follow from
  ``roofs.py``'s table rather than from a hand-written list.
* **Every period parameter is intersected with its own ``as_of``.** A sampled
  window that runs past the case's own cut asks about days the case cannot see.

The first and the last are the same arithmetic over two different surfaces, and
they stay separate because the failures are different: the first is an argument
the template asks for, the last is a value the sampler drew.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from water_assistant_agent.assistant.context import ScenarioContext
from water_assistant_agent.assistant.tools.gr2l_client import NON_MODELLABLE_ROOFS
from water_assistant_agent.assistant.tools.roofs import (
    MODELLED_ROOFS,
    ROOFS,
    resolve_roof,
    roofs_with_column,
)
from water_assistant_agent.assistant.tools.site import SITE_TIMEZONE
from water_assistant_agent.assistant.tools.weather_client import (
    InvalidWindowError,
    resolve_window,
)
from water_assistant_agent.assistant.toolset import GREEN_ROOF_TOOL

_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_NON_MODELLABLE: tuple[str, ...] = tuple(
    name for name in ROOFS if name in NON_MODELLABLE_ROOFS
)

ROOF_POOLS: Mapping[str, tuple[str, ...]] = {
    "P1": roofs_with_column("swc"),
    "P1f": roofs_with_column("outflow"),
    "P2": tuple(name for name in MODELLED_ROOFS if name not in _NON_MODELLABLE),
    "non_modellable": _NON_MODELLABLE,
}
"""The catalog's sampling pools, read off the roof table rather than hand-listed.

``P1`` is the full roster, ``P1f`` its flux-instrumented subset, ``P2`` the
substrate roofs both water-balance tools cover, ``non_modellable`` the two the
tools decline and family I samples from. Each is a projection of ``roofs.py``, so
a roof that gains or loses a column moves the pool with it and cannot be
forgotten in a second place.
"""


@dataclass(frozen=True)
class Violation:
    """One broken invariant: which check, and what it found."""

    check: str
    detail: str


class CaseAssertionError(AssertionError):
    """A case broke an invariant, so it is not runnable and is not scored.

    Raised rather than recorded on purpose. This is not a candidate failing — it
    is the testbed asking a question it cannot answer, and a number computed over
    it would be a number about nothing.
    """

    def __init__(self, case_id: str, violations: Sequence[Violation]) -> None:
        self.case_id = case_id
        self.violations = tuple(violations)
        listed = "\n".join(f"  - {v.check}: {v.detail}" for v in self.violations)
        super().__init__(f"Case {case_id or '<unnamed>'} is not runnable:\n{listed}")


def assert_model_replays(ctx: ScenarioContext) -> None:
    """*ctx* cannot reach GR2L live, or raise.

    Checked on ``ctx.cache`` rather than on the client because that is where the
    property lives: ``run_gr2l`` passes the context's cache and no flag at all, so
    a cache that refuses a live fetch is the one thing every model hop goes
    through. :class:`~harness.run_case.ReplayCache` declares ``refuses_live``; a
    plain :class:`~..cache.ResponseCache` does not, and neither does ``None``.

    **It is the model half only, and that is the whole change of T146.** The
    weather half no longer reads ``ctx.cache``: it holds its own recording cache,
    keyed per day, and fills a day the capture pass did not reach rather than
    failing the case over it. So this asserts what replay still means —
    ``(rows, parameters)`` in, a committed response out, no service consulted —
    for the one component whose determinism the cache carries, and asserts
    nothing about the network as a whole. A pass that wants *that* blocks the
    socket, which is what ``scripts/capture_cache.py --verify`` does.
    """
    if not getattr(ctx.cache, "refuses_live", False):
        raise CaseAssertionError(
            "",
            [
                Violation(
                    check="model_replays",
                    detail=(
                        "the context is bound to a cache that would fill a GR2L "
                        f"miss live ({type(ctx.cache).__name__}); replay needs one "
                        "that refuses"
                    ),
                )
            ],
        )


def check_inputs(
    inputs: Mapping[str, Any], *, roof_pool: str | None = None
) -> tuple[Violation, ...]:
    """The two invariants a case's ``inputs`` half carries on its own.

    *roof_pool* is the template's, since the pool is a property of the family and
    the case envelope has no third channel to carry it (§6.1). Omitted, the roof
    check does not run — an un-pooled template names no roof.
    """
    as_of = site_day(inputs["as_of"])
    params = inputs.get("params") or {}
    violations = [*_check_period_params(params, as_of)]
    if roof_pool is not None:
        violations.extend(_check_roof_pool(params, roof_pool))
    return tuple(violations)


def check_calls(
    calls: Iterable[Mapping[str, Any]], as_of: datetime | str
) -> tuple[Violation, ...]:
    """Retrospective comparison windows in *calls* end at or before *as_of*.

    *calls* are ``{"name": …, "args": {…}}`` objects — a case's
    ``expected_tool_calls``, which is what this is for. It is deliberately not
    run over a rollout's own trajectory: an agent that asks to compare against
    days the record does not hold has fumbled, and a fumble is scored, not
    asserted away (``decisions.md`` § Tool errors and harness exclusion).
    """
    day = site_day(as_of)
    violations: list[Violation] = []
    for call in calls:
        if call.get("name") != GREEN_ROOF_TOOL:
            continue
        args = call.get("args") or {}
        if not args.get("evaluate_against_measured"):
            continue
        try:
            end = _window_end(args, day)
        except InvalidWindowError as exc:
            violations.append(
                Violation(
                    check="retrospective_window_within_as_of",
                    detail=f"the comparison window does not resolve: {exc}",
                )
            )
            continue
        if end > day:
            violations.append(
                Violation(
                    check="retrospective_window_within_as_of",
                    detail=(
                        f"the comparison window ends {end.isoformat()}, past the "
                        f"case's as_of ({day.isoformat()})"
                    ),
                )
            )
    return tuple(violations)


def check_case(
    case: Mapping[str, Any], *, roof_pool: str | None = None
) -> tuple[Violation, ...]:
    """Every case-level invariant over one whole record, ``expectations`` included."""
    inputs = case["inputs"]
    expectations = case.get("expectations") or {}
    return (
        *check_inputs(inputs, roof_pool=roof_pool),
        *check_calls(expectations.get("expected_tool_calls") or (), inputs["as_of"]),
    )


def assert_case(case: Mapping[str, Any], *, roof_pool: str | None = None) -> None:
    """:func:`check_case`, raising :class:`CaseAssertionError` on any violation."""
    violations = check_case(case, roof_pool=roof_pool)
    if violations:
        raise CaseAssertionError(str(case["inputs"].get("case_id", "")), violations)


def assert_inputs(inputs: Mapping[str, Any], *, roof_pool: str | None = None) -> None:
    """:func:`check_inputs`, raising — the preflight a rollout itself can run.

    A rollout is handed ``inputs`` and nothing else, so this is the half of
    :func:`assert_case` that ``run_case`` can enforce. The other half needs
    ``expectations`` and belongs to the generator and to whoever loads a split.
    """
    violations = check_inputs(inputs, roof_pool=roof_pool)
    if violations:
        raise CaseAssertionError(str(inputs.get("case_id", "")), violations)


def _check_period_params(
    params: Mapping[str, Any], as_of: date
) -> Iterable[Violation]:
    """No sampled parameter names a day past the case's own cut.

    Read off the *values* rather than off a parameter's name, because the name is
    the template author's (``event``, ``window``, ``month``) and the invariant is
    not. Any ``YYYY-MM-DD`` a value carries counts, whether it is the value
    itself, one end of a ``start..end`` range, or an entry in a list or an
    object; anything that is not a day is passed over.
    """
    for name, value in params.items():
        days = _days_in(value)
        if days and max(days) > as_of:
            yield Violation(
                check="period_param_within_as_of",
                detail=(
                    f"parameter {name}={value!r} reaches {max(days).isoformat()}, "
                    f"past the case's as_of ({as_of.isoformat()})"
                ),
            )


def _check_roof_pool(params: Mapping[str, Any], pool: str) -> Iterable[Violation]:
    """Every roof a case names is in its family's pool.

    Any parameter value that resolves to a roof segment is checked, not only one
    called ``roof``: a comparison template names two, and a pool violated under
    the name ``roof_b`` is the same defect.
    """
    members = pool_members(pool)
    for name, value in params.items():
        if not isinstance(value, str):
            continue
        segment = resolve_roof(value)
        if segment is None or segment.name in members:
            continue
        yield Violation(
            check="roof_pool_respected",
            detail=(
                f"parameter {name}={value!r} resolves to {segment.name}, which is "
                f"not in pool {pool} ({', '.join(members)})"
            ),
        )


def pool_members(pool: str) -> tuple[str, ...]:
    """The canonical roof names in *pool*, raising on a pool that does not exist."""
    try:
        return ROOF_POOLS[pool]
    except KeyError:
        valid = ", ".join(ROOF_POOLS)
        raise ValueError(f"Unknown roof pool {pool!r}. Valid pools: {valid}.") from None


def _window_end(args: Mapping[str, Any], day: date) -> date:
    """The last day the call's window covers, through layer 1's own resolver.

    :func:`~..tools.weather_client.resolve_window` is what both tool wrappers
    call before touching a client, so a relative window and the dates it denotes
    resolve identically here and there — the property the case schema's
    ``resolve: "window"`` flag names. Re-deriving it would let the two drift and
    still pass.
    """
    _, end = resolve_window(
        args.get("start_date"),
        args.get("end_date"),
        args.get("past_days"),
        args.get("forecast_days"),
        today=day,
    )
    return date.fromisoformat(end)


def _days_in(value: Any) -> list[date]:
    """Every ``YYYY-MM-DD`` day *value* carries, however it is nested."""
    if isinstance(value, str):
        return [
            date.fromisoformat(part)
            for part in value.split("..")
            if _ISO_DAY.match(part.strip())
        ]
    if isinstance(value, Mapping):
        return [day for item in value.values() for day in _days_in(item)]
    if isinstance(value, (list, tuple)):
        return [day for item in value for day in _days_in(item)]
    return []


def site_day(as_of: datetime | str) -> date:
    """The site's own calendar day for *as_of*.

    The site's day, not the host's and not UTC: a stamp at 00:30 Berlin time is
    the Berlin day it names, and reading it anywhere else moves the cut by one
    day for half the year (``decisions.md`` § The day boundary).
    """
    instant = as_of if isinstance(as_of, datetime) else datetime.fromisoformat(as_of)
    if instant.tzinfo is None:
        raise ValueError(f"A case's as_of must carry a UTC offset, got {as_of!r}.")
    return instant.astimezone(ZoneInfo(SITE_TIMEZONE)).date()
