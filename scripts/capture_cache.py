"""T116's capture pass: fill ``eval/cache/`` for the committed suite, then prove it replays.

**What a capture pass captures.** Not a rollout — an *oracle*. A case's gold
answer is only ground truth relative to the responses it was computed from, so
the requests that must be committed are the ones the oracle made, and the oracle
is the only thing that can say what those are. Driving agent rollouts instead
would capture whatever windows the model happened to pick on the day, which is a
different set, non-deterministic, and not the set replay needs
(``decisions.md`` § The response cache: "capture runs over the oracle's window").

Re-running an oracle is a re-run of frozen code, not a rewrite of it. It also
buys a second thing for free: the recomputed answer is compared against the
committed one, so this pass reports whether the suite's ground truth still
reproduces against the live services rather than assuming it.

**One event loop for the whole pass**, which is the hazard T115 found and left
here. ``gr2l_client`` keeps its ``httpx.AsyncClient`` in a module-level singleton
and httpx binds a connection pool to the loop that created it, so a driver
calling ``asyncio.run`` per case dies on its second live GR2L call with
``RuntimeError: Event loop is closed`` — surfacing as an ``upstream`` error
through ``CacheMissError``. :func:`main` is therefore entered once and every case
runs inside it. The fix is here rather than in ``gr2l_client``, which is frozen.

**Two things the oracles do not cover on their own.**

*Family H.* ``t24a_plot_request`` resolves a vocabulary and a roof and fetches
nothing — every argument fault and the one scope limit are settled before the
first query, so the oracle records no entry. But a rollout that draws the
``model_overlay`` variant runs GR2L over the month, through ``run_roof_model``
exactly as ``plot.py``'s ``_PlotSources.model`` does. That call is made here, so
family H is part of the capture surface wherever a plot fetches weather or GR2L
itself.

*The neighbourhood.* The cache is keyed on the request, so a candidate that reads
"the next d days" one day differently from the oracle misses, and a miss in
replay excludes the rollout — biasing the exclusion rate towards candidate
consistency rather than harness health. T107's pilot found this, and
``scripts/prewarm_pilot_cache.py`` was the pilot-sized answer to it — retired
here, because this pass does the same job over the whole suite. So each
day-count case is also answered at
d ± 1, 2, 3. The neighbours are warmed **through the oracle itself**, by moving
the parameter and letting the oracle resolve the window, rather than by restating
each family's horizon rule in a second place where it could come to mean
something else. The width was ±1 until T134: P8c lost more than half the holdout
to misses outside it, so the widening is registered with that run rather than
tuned quietly (:data:`NEIGHBOURHOOD`).

**The neighbourhood reaches a template only through a parameter, and T140 measured
what that leaves out.** Moving ``params["d"]`` warms nothing for a template that
names no day count, and running the oracle warms nothing for a template whose
oracle fetches nothing — so the width was never the whole story. Measured against
the committed cache over the holdout, "how many of a template's eight cases hold
the window a rollout resolves at that span":

============  ====================================  =========================
Template      Coverage by span (1, 2, 3, 4, 5 …)    Excluded in T134's run
============  ====================================  =========================
T22           0, **8**, 2, 3, 2 …                   46 / 48
T18b          1, 1, 1, 0, 0 …                       33 / 48
T26           1, 5, 7, 7, 7 …                       39 / 48
T20           2, 2, 5, 7, 7 …                       12 / 48
============  ====================================  =========================

Three causes, and the last row is the control that confirms all three. **T22 gets
no neighbourhood at all**: its window is a literal ``forward_window(2, ctx)``
inside the oracle and its params are ``{roof, a}``, so :func:`neighbours` returns
nothing and coverage is a spike at exactly gold's window. **T18b's oracle fetches
nothing by design** — the gold trajectory is empty and "the correct trajectory
makes no call" (``eval/oracles/weather.py``) — so warming *through* the oracle
records nothing either, while the natural rollout consults the weather tool before
abstaining. **T26's GR2L key carries axes no day count moves**: ``data[]`` with the
forcing merged in, plus ``albedo`` and the seed, so only the forced run at gold's
exact ``(mm, offset, albedo, window)`` is committed and the unforced baseline the
"fetch-then-substitute" route issues is present by accident. T20 loses least
because its oracle over-fetches ``d + 2`` days *and* carries a ``d``, so the sweep
happens to cover spans d−1…d+5.

What that costs is not throughput. On T18b a rollout that abstains blind is
scored while one that checks the tool first is excluded — and abstention is what
T18b measures; on T22 the survivors are the rollouts that resolved gold's window,
so the answer metric there is conditioned on trajectory agreement. This is the
hazard ``decisions.md § The search records where the measurement run replays``
states in so many words, fixed on the search path and left standing on this one.

:func:`warm_rollout_windows` is the answer, and it warms the **window** rather
than a parameter: every forward span a rollout could resolve for the case
(:func:`forward_windows`), and — for a case whose answer came from GR2L — both
the un-overridden baseline run and the case's own override over each. It is
best-effort and outside what ``--verify`` checks, exactly as :func:`neighbours`
already is.

**Weather stopped being warmed by the window at all** (T146). It is cached per
calendar day now, so what a rollout can reach is the band around ``as_of``
(:data:`WARM_BAND_DAYS`) rather than the windows over it, and one fetch across
that band warms every window any candidate can assemble from it — backward ones
included, which no sweep here reached before. Two things follow. The forward
span list above is GR2L's alone, since GR2L is still keyed on the whole fetched
row array and has no day to decompose into. And a weather day this pass misses
is no longer fatal downstream: the measurement path fills and records one
(``harness/run_case.py``), so this warm is now about keeping the measurement
offline rather than about keeping cases from being excluded.

Run::

    uv run python scripts/capture_cache.py            # record into eval/cache/
    uv run python scripts/capture_cache.py --verify    # replay, asserting no live call
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import httpx  # noqa: E402

from eval.oracles import ORACLES  # noqa: E402
from eval.oracles.base import OracleInputError  # noqa: E402
from eval.oracles.counterfactual import rain_forcing  # noqa: E402
from eval.oracles.model_chain import modellable_roof  # noqa: E402
from eval.oracles.presentation import MODEL_OVERLAY, _series_for  # noqa: E402
from eval.oracles.sql import month_window  # noqa: E402
from harness.assertions import assert_model_replays  # noqa: E402
from harness.run_case import EVAL_CACHE_DIR, _as_instant, make_case_context  # noqa: E402
from water_assistant_agent.assistant.context import ScenarioContext  # noqa: E402
from water_assistant_agent.assistant.tools.gr2l import run_roof_model  # noqa: E402
from water_assistant_agent.assistant.tools.gr2l_client import _get_client  # noqa: E402
from water_assistant_agent.assistant.tools.plot import prepare_series  # noqa: E402
from water_assistant_agent.assistant.tools.weather_client import (  # noqa: E402
    beyond_horizon,
    resolve_window,
)

CASES_DIR = REPO_ROOT / "eval" / "cases"

SPLITS: tuple[str, ...] = ("train", "test_seen", "test_unseen")
"""The suite this pass captures.

``pilot.json`` is deliberately absent, and stays absent. It was P6's freeze
gate — 14 hand-instantiated cases run to prove the harness end to end — and that
gate has been passed; nothing downstream reads it. P8's search and measurement
load ``train.json`` as MLflow ``train_data`` and report on the two test splits,
and the one test that reads the pilot file needs no cache at all, because T24a's
oracle resolves scope without fetching. Capturing for it would pair a current
cache with answers computed against the pre-2026-08-24 GR2L build, and re-running
it would re-measure a gate rather than measure anything. See T116's notes.
"""

DAY_COUNT_PARAMS: tuple[str, ...] = ("d", "ahead_days")
"""The parameters that name a horizon the candidate passes through verbatim.

Read off the emitted cases rather than off the templates: these are the two keys
whose value becomes a day count in ``resolve_window``, and every other parameter
in the suite either selects a roof, names a completed period, or states a
quantity that does not move the window.
"""

NEIGHBOURHOOD: tuple[int, ...] = (-3, -2, -1, 1, 2, 3)
"""How far either side of the case's own horizon to warm, beyond the case itself.

**±1 was sized on the pilot and the measurement run said it was too narrow.**
The pilot observed one candidate read "the next d days" as reaching d days *past*
today rather than d days *including* it, which is a ±1 error, and ±1 is what T116
warmed. P8c then lost **30 of 56 test_unseen cases in one arm and 28 in the
other** to replay misses, almost all ``upstream``, and two of that split's seven
templates were measured in neither arm — the holdout's population, not a rounding
error. ±3 is the widening T134 registers: still the same mechanism, still warmed
through each family's own horizon rule, and it costs recording time rather than
model spend.

It is still not a claim that no candidate asks for anything else. A miss remains
possible and still excludes, which is why §7 publishes the exclusion count per
arm rather than assuming this pass drove it to zero — and why the count is read
against the *other* arm's rather than against zero.
"""

WARM_BAND_DAYS: int = 16
"""How far either side of a case's ``as_of`` the **weather** warm reaches, in days.

T146's half of the sweep, and the reason it is a band of days rather than a list
of windows. Weather is cached per calendar day and a window is assembled from
days (``weather_client.ArchiveWeatherClient``), so what a rollout can reach is no
longer a plane of ``(start, end)`` pairs — 17 forward spans crossed with every
backward one — but the days those pairs are drawn from, which is a list of
``2 × 16 + 1``. One ``ctx.weather.fetch`` over the whole band warms all of them,
in one Archive request per contiguous gap.

Sixteen forward because that is :data:`FORECAST_HORIZON_DAYS`, the tool's own
scope limit: a window ending later is typed ``not_available`` before it fetches,
so there is no request past it to warm. Sixteen backward for symmetry rather than
from a rule — nothing bounds how far back a window may reach — and it covers the
catalog's longest horizon (10 days) with room. The backward side is what
:func:`warm_rollout_windows` could not reach at all before this: its sweep ran
forward only, and T25's own gold route is ``past_days=1, forecast_days=2``, whose
neighbours missed on three of five cases (``findings.md``). Closing that axis
used to mean roughly doubling the committed cache, because it doubled a plane;
per day it costs 16 entries.
"""

FORWARD_SPAN_CEILING: int = 8
"""How many forward spans to warm for a case whose horizon no parameter names.

T22 is why this number exists. Its window is a literal ``forward_window(2, ctx)``
written into the oracle rather than a ``{d}`` the case carries, so there is no
parameter for :data:`NEIGHBOURHOOD` to move and the only span ever warmed was
gold's own. Rather than write "T22 means two days" here — a second place the
family's own horizon rule could come to mean something else, which this module's
header refuses for the neighbours — every such case is warmed from span 1 up to
this ceiling, which contains gold's span without naming it.

Eight rather than the catalog's longest horizon (T15b's and T23's 10): the
templates with no day count all ask about *tomorrow*, and a rollout that reads
"tomorrow" as more than a week is not the near-miss this exists to absorb. A case
that does carry a day count is warmed to ``d + max(NEIGHBOURHOOD)`` instead, so
the ceiling never truncates a horizon the suite actually draws.
"""

MODEL_PIN: str = "gr2l_canary"
"""The pin that marks a case whose answer came from GR2L, and the warm's own gate.

Read off the committed ``expectations.pins`` rather than off a list of template
ids: a case is stamped with the model's canary exactly when its oracle ran the
model (``eval/oracles/pins.py``), which is the same condition under which a
rollout will run it. A template list here would be a second answer to a question
the emitted cases already answer, and it would go stale the first time a family
gained or lost a model call.
"""


class LiveCallAttempted(AssertionError):
    """A replay pass tried to open a socket, which is the one thing it may not do."""


def load_cases() -> list[dict[str, Any]]:
    """Every committed case of :data:`SPLITS`, in file order."""
    cases: list[dict[str, Any]] = []
    for split in SPLITS:
        cases.extend(json.loads((CASES_DIR / f"{split}.json").read_text(encoding="utf-8")))
    return cases


def cache_keys() -> set[str]:
    return {path.name for path in EVAL_CACHE_DIR.glob("*.json")}


def classify(entry: Mapping[str, Any]) -> str:
    """``gr2l`` or ``archive``, by request shape — the two services this cache serves.

    GR2L requests carry ``data[]`` plus the roof parameters; Open-Meteo Archive
    requests carry a URL and a query mapping. Shape rather than a recorded label,
    because the entries are written by the clients and no client writes one.
    """
    request = entry.get("request")
    if not isinstance(request, Mapping):
        return "unknown"
    if {"data", "SH", "Ssubmax", "Ssubmin", "kg", "albedo"} <= set(request):
        return "gr2l"
    return "archive" if "url" in request else "unknown"


def census() -> dict[str, int]:
    """How many entries of each service the cache currently holds."""
    counts: dict[str, int] = {}
    for path in EVAL_CACHE_DIR.glob("*.json"):
        kind = classify(json.loads(path.read_text(encoding="utf-8")))
        counts[kind] = counts.get(kind, 0) + 1
    return counts


async def capture_one(
    inputs: Mapping[str, Any], ctx: ScenarioContext
) -> tuple[str, Any]:
    """Answer one case through its oracle, plus whatever the case's *rollout* fetches.

    Returns ``(outcome, answer)``. ``outcome`` is ``answered`` when the oracle
    produced a value, ``refused`` when it raised :class:`OracleInputError` — which
    is a legitimate materialization outcome for the abstention templates — and
    ``failed`` for anything else, which is the pass's own error to report.
    """
    template = str(inputs["template_id"])
    try:
        result = await ORACLES[template](inputs, ctx)
    except OracleInputError as exc:
        return f"refused: {exc}", None

    await capture_rollout_extras(inputs, ctx)
    return "answered", result.answer


async def capture_rollout_extras(inputs: Mapping[str, Any], ctx: ScenarioContext) -> None:
    """The fetches a *rollout* makes that the case's oracle does not.

    Family H only, and only its model overlay. ``t24a_plot_request`` settles the
    vocabulary and the scope limit before any fetch, so it records nothing; the
    rollout that draws the same request runs GR2L over the month, and its weather
    with it. The measured pair reads DuckDB only and the non-modellable overlay is
    declined before it fetches, so neither has anything to add.

    The series come from the oracle's own :func:`_series_for` and go through the
    tool's own :func:`prepare_series`, then into ``run_roof_model`` with the
    arguments ``plot.py``'s ``_PlotSources.model`` passes. Nothing about which
    series a variant denotes, or how a roof spelling resolves, is restated here —
    a second opinion about either would capture a request the rollout never makes,
    which replays as a miss on the case this exists to cover.
    """
    params = inputs.get("params") or {}
    if str(inputs["template_id"]) != "T24a" or params.get("variant") != MODEL_OVERLAY:
        return
    start, end = month_window(str(params["month"]))
    for spec in _series_for(MODEL_OVERLAY, params):
        prepared = prepare_series(spec, start.isoformat(), end.isoformat())
        if prepared.spec.source != "model":
            continue
        await run_roof_model(
            ctx,
            str(prepared.roof.name) if prepared.roof is not None else "",
            start.isoformat(),
            end.isoformat(),
            initial_soil_moisture_pct=prepared.spec.initial_soil_moisture_pct,
            albedo=prepared.spec.albedo,
            forcings=prepared.forcings,
        )


def forward_windows(
    inputs: Mapping[str, Any], ctx: ScenarioContext
) -> list[tuple[str, str]]:
    """Every forward window a rollout could resolve for this case, shortest first.

    All of them open on the case's own day, because that is what
    ``resolve_window(forecast_days=F, today=A)`` produces and ``forecast_days`` is
    the relative form the tool documents. The span runs from **one** rather than
    from the case's own horizon: a candidate reading "the next three days" as
    "tomorrow" is the same near-miss as one reading it as four, and starting at 1
    is what lets a case with no day count be warmed at all
    (:data:`FORWARD_SPAN_CEILING`).

    Windows past the tool's 16-day horizon are dropped rather than warmed: there
    the tool types ``not_available`` before it fetches, so no request exists to
    record and a warmed entry would answer a call nothing makes.
    """
    params = inputs.get("params") or {}
    name = next((key for key in DAY_COUNT_PARAMS if key in params), None)
    value = params.get(name) if name is not None else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        ceiling = FORWARD_SPAN_CEILING
    else:
        ceiling = value + max(NEIGHBOURHOOD)

    today = ctx.as_of.date()
    windows: list[tuple[str, str]] = []
    for span in range(1, ceiling + 1):
        start, end = resolve_window(forecast_days=span, today=today)
        if beyond_horizon(end, today):
            break
        windows.append((start, end))
    return windows


def model_overrides(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """The case's own GR2L override arguments, in the tool's own vocabulary.

    Three parameters name an override and each maps to one argument of
    ``run_roof_model``: ``a`` to ``albedo``, ``x`` to
    ``initial_soil_moisture_pct``, and ``mm`` with ``offset`` to a ``forcings``
    overlay built by the oracles' own :func:`~eval.oracles.counterfactual.rain_forcing`
    rather than by a second spelling of ``{"precip": {day: mm}}`` here.

    Returns ``{}`` for a case that overrides nothing, which is what makes the
    baseline the only run family D needs.
    """
    params = inputs.get("params") or {}
    overrides: dict[str, Any] = {}
    if "a" in params:
        overrides["albedo"] = float(params["a"])
    if "x" in params:
        overrides["initial_soil_moisture_pct"] = float(params["x"])
    if "mm" in params and "offset" in params:
        forced_day = _as_instant(inputs["as_of"]).date() + timedelta(
            days=int(params["offset"])
        )
        overrides["forcings"] = rain_forcing(forced_day, float(params["mm"]))
    return overrides


def named_roofs(inputs: Mapping[str, Any]) -> list[str]:
    """The modellable roofs this case names, deduplicated in the order it names them.

    ``roof``, ``roof_a`` and ``roof_b`` are the three keys a template uses; a
    spelling the model does not serve is dropped through the oracles' own
    :func:`~eval.oracles.model_chain.modellable_roof`, which is the tool's scope
    table read the way the tool reads it.
    """
    params = inputs.get("params") or {}
    roofs: list[str] = []
    for key in ("roof", "roof_a", "roof_b"):
        if key not in params:
            continue
        try:
            roof_type = modellable_roof(params[key], str(inputs["template_id"]))
        except OracleInputError:
            continue
        if roof_type not in roofs:
            roofs.append(roof_type)
    return roofs


async def warm_rollout_windows(
    inputs: Mapping[str, Any], expectations: Mapping[str, Any], ctx: ScenarioContext
) -> tuple[int, int]:
    """Warm the requests a rollout issues that no run of this case's oracle does.

    Two warms, and the module header measures what each is for. They no longer
    share a surface: weather is warmed as a **band of days** and GR2L over the
    forward window list (:func:`forward_windows`), because since T146 only one of
    the two is still keyed on the window.

    *The weather over the whole band* (:data:`WARM_BAND_DAYS`), whether or not
    this case's oracle fetched any of it. T18b's oracle fetches nothing at all —
    its gold trajectory is empty — so before this the eight T18b cases had no
    entry behind them and every rollout that consulted the tool before abstaining
    was excluded, which selected against the very behaviour the template
    measures. It runs for **every** case and not only the forward-facing
    families, which is deliberate rather than sloppy: a retrospective question
    answered by a candidate that fetched the forecast anyway is a fumble
    ``decisions.md`` § Tool errors and harness exclusion wants *scored*, and
    leaving those days cold used to convert it into an exclusion instead — the
    same pathology one family up, arrived at from the other side. The cost is one
    Open-Meteo GET per contiguous gap in the band, which is one on a cold case
    and none on a case whose neighbours have already been warmed.

    *Both GR2L runs over every forward window*, for a case stamped
    :data:`MODEL_PIN`: the un-overridden baseline, which is the fetch-then-
    substitute route family G's own notes call a valid trajectory, and the case's
    own override, which is the call gold makes and which was warmed at exactly one
    span for any template naming no day count.

    Returns:
        ``(warmed, dropped)`` — requests recorded or already held, and requests
        this pass asked for and did not get.

    Best effort throughout, on :func:`neighbours`' terms: a window the record
    cannot serve, a roof the model declines or a seed that has gone stale is one a
    candidate asking for it would be answered ``not_available`` on anyway, and a
    warm that raises is not this pass's finding.

    **The dropouts are counted, though, which the neighbours' are not.** T140's
    own pass lost seven windows across two cases to what looks like an Open-Meteo
    rate limit, and a silent swallow made that indistinguishable from a horizon
    the record genuinely cannot serve — a warm gap and a scope limit reported the
    same way, which is to say not at all. The second return value is what lets a
    re-run be judged rather than guessed at.

    **The weather sweep now reaches backward too**, which it did not until T146
    and which was a stated gap while it did not. ``resolve_window`` also takes
    ``past_days``, and T25's own gold route is ``past_days=1, forecast_days=2`` —
    a window opening the day *before* ``as_of``, which nothing here warmed;
    measured, its ``p1f1`` and ``p1f3`` neighbours missed on three of five cases
    (``findings.md``). It stayed open because closing it was a second axis of a
    plane. It is closed here because the axis is now a day.

    **The GR2L sweep still reaches forward only**, and there the old reason
    stands unchanged: GR2L is keyed on ``data[]`` plus its parameters — the whole
    fetched row array — so a window one day out is an unrelated key rather than a
    nearby one, and no decomposition into days exists to exploit.
    """
    day = ctx.as_of.date()
    warmed = dropped = 0
    try:
        # One call, and the client turns it into one Archive request per gap:
        # every day any window this case can resolve is assembled from.
        await ctx.weather.fetch(
            start_date=(day - timedelta(days=WARM_BAND_DAYS)).isoformat(),
            end_date=(day + timedelta(days=WARM_BAND_DAYS)).isoformat(),
        )
        warmed += 1
    except Exception:  # noqa: BLE001 - best effort by design
        dropped += 1

    if MODEL_PIN not in (expectations.get("pins") or {}):
        return warmed, dropped

    windows = forward_windows(inputs, ctx)
    overrides = model_overrides(inputs)
    # The baseline first and always: family D names no override, and family G's
    # rollout reaches the override through it whenever it fetches before forcing.
    variants: list[dict[str, Any]] = [{}] if not overrides else [{}, overrides]
    for roof_type in named_roofs(inputs):
        for start, end in windows:
            for variant in variants:
                try:
                    await run_roof_model(ctx, roof_type, start, end, **variant)
                    warmed += 1
                except Exception:  # noqa: BLE001 - best effort by design
                    dropped += 1
    return warmed, dropped


def neighbours(inputs: Mapping[str, Any]) -> list[dict[str, Any]]:
    """*inputs* re-stated at each neighbouring horizon, or empty when it has none.

    The parameter is moved and the oracle re-resolves the window from it, so the
    warmed request is whatever that family's own horizon rule produces for the
    neighbouring day count — never a window this script computed.
    """
    params = inputs.get("params") or {}
    name = next((key for key in DAY_COUNT_PARAMS if key in params), None)
    if name is None:
        return []
    value = params[name]
    if isinstance(value, bool) or not isinstance(value, int):
        return []
    variants = []
    for delta in NEIGHBOURHOOD:
        span = value + delta
        if span < 1:
            continue
        variants.append({**inputs, "params": {**params, name: span}})
    return variants


async def record(cases: list[dict[str, Any]]) -> int:
    """The capture half: answer every case live and record what it fetched."""
    before = cache_keys()
    print(f"Capturing {len(cases)} cases into {EVAL_CACHE_DIR.relative_to(REPO_ROOT)}/")
    print(f"  {len(before)} entries already committed: {census()}\n")

    contexts: dict[str, ScenarioContext] = {}

    def context_for(as_of: str) -> ScenarioContext:
        # One context per distinct `as_of`, reused: each opens a DuckDB connection
        # and builds the as-of views, and neighbours share their case's day.
        if as_of not in contexts:
            contexts[as_of] = make_case_context(_as_instant(as_of), allow_live=True)
        return contexts[as_of]

    answered = refused = failed = mismatched = warmed = dropped = 0
    for case in cases:
        inputs = case["inputs"]
        case_id = inputs["case_id"]
        ctx = context_for(inputs["as_of"])
        try:
            outcome, answer = await capture_one(inputs, ctx)
        except Exception as exc:  # noqa: BLE001 - the pass reports, it does not stop
            failed += 1
            print(f"  {case_id}: FAILED {type(exc).__name__}: {exc}")
            continue

        if outcome.startswith("refused"):
            refused += 1
            print(f"  {case_id}: {outcome}")
            continue

        answered += 1
        expected = case["expectations"]["answer"]
        if not same_answer(answer, expected):
            mismatched += 1
            print(f"  {case_id}: ANSWER MOVED committed={expected!r} recomputed={answer!r}")

        for variant in neighbours(inputs):
            try:
                await ORACLES[str(variant["template_id"])](variant, ctx)
                warmed += 1
            except Exception:  # noqa: BLE001 - best effort by design
                # A neighbour that cannot be answered is not a failure: the point
                # is a warm cache, and a horizon the record cannot serve is one a
                # candidate asking for it would be abstained on anyway.
                continue

        # The half no run of the oracle reaches, whatever its parameter is moved
        # to: the window itself (T140).
        recorded, lost = await warm_rollout_windows(inputs, case["expectations"], ctx)
        warmed += recorded
        dropped += lost

    added = cache_keys() - before
    print(
        f"\n{answered} answered, {refused} refused, {failed} failed, "
        f"{warmed} requests warmed, {dropped} dropped"
    )
    print(f"{len(added)} new entries ({len(before)} → {len(before) + len(added)}): {census()}")
    if mismatched:
        print(f"\n{mismatched} case(s) recomputed to a different answer than the one committed.")
        print("The suite's ground truth no longer reproduces; do not commit this cache.")
        return 1
    if failed:
        print(f"\n{failed} case(s) could not be answered at all.")
        return 1
    print("\nEvery case reproduced its committed answer against the live services.")
    return 0


def same_answer(recomputed: Any, committed: Any) -> bool:
    """Whether a recomputed answer is the committed one.

    Exact for everything but floats, which are compared on their serialized form:
    the case files carry what ``json.dumps`` wrote, so a float that round-trips to
    the same text is the same answer as far as anything downstream can tell.
    """
    if isinstance(recomputed, float) or isinstance(committed, float):
        return json.dumps(recomputed) == json.dumps(committed)
    return recomputed == committed


async def verify(cases: list[dict[str, Any]]) -> int:
    """The replay half: answer every case again with the network physically blocked.

    Two independent guarantees, because one of them is a claim about a flag and
    the other is a claim about a socket. The context is bound to a
    ``ReplayCache``, which :func:`assert_model_replays` checks and which converts a
    miss into a hard failure; and ``httpx.AsyncClient.send`` is replaced for the
    duration, so a call that found some other way out would raise here rather than
    quietly succeeding. Replay passing under the second is what "zero live calls"
    means as a fact rather than as a configuration.
    """
    before = cache_keys()
    print(f"Replaying {len(cases)} cases with the network blocked\n")

    calls: list[str] = []

    async def blocked(self: httpx.AsyncClient, request: httpx.Request, **_: Any) -> Any:
        calls.append(f"{request.method} {request.url}")
        raise LiveCallAttempted(f"replay attempted a live call: {request.method} {request.url}")

    original = httpx.AsyncClient.send
    httpx.AsyncClient.send = blocked  # type: ignore[method-assign]
    contexts: dict[str, ScenarioContext] = {}
    answered = refused = missed = 0
    try:
        for case in cases:
            inputs = case["inputs"]
            as_of = inputs["as_of"]
            if as_of not in contexts:
                ctx = make_case_context(_as_instant(as_of), allow_live=False)
                assert_model_replays(ctx)
                contexts[as_of] = ctx
            try:
                await ORACLES[str(inputs["template_id"])](inputs, contexts[as_of])
                # Replayed as well as recorded: family H's overlay entry exists
                # only because a rollout fetches it, so leaving it out here would
                # verify every entry except the one nothing else covers.
                await capture_rollout_extras(inputs, contexts[as_of])
                answered += 1
            except OracleInputError:
                refused += 1
            except Exception as exc:  # noqa: BLE001 - a miss is the finding
                missed += 1
                print(f"  {inputs['case_id']}: MISS {type(exc).__name__}: {exc}")
    finally:
        httpx.AsyncClient.send = original  # type: ignore[method-assign]

    recorded = cache_keys() - before
    print(f"\n{answered} answered, {refused} refused, {missed} unfillable")
    print(f"live calls attempted: {len(calls)}")
    print(f"entries recorded during replay: {len(recorded)}")
    if calls or missed or recorded:
        print("\nReplay is not clean.")
        return 1
    print("\nEvery case replayed from the committed cache with zero live calls.")
    return 0


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="replay the suite from the committed cache instead of recording it",
    )
    args = parser.parse_args(argv)

    cases = load_cases()
    if args.verify:
        return await verify(cases)
    try:
        return await record(cases)
    finally:
        # The module-level client belongs to this loop; closing it here keeps the
        # loop's shutdown from reporting an un-awaited connection pool.
        await _get_client().aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
