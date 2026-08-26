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
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import httpx  # noqa: E402

from eval.oracles import ORACLES  # noqa: E402
from eval.oracles.base import OracleInputError  # noqa: E402
from eval.oracles.presentation import MODEL_OVERLAY, _series_for  # noqa: E402
from eval.oracles.sql import month_window  # noqa: E402
from harness.assertions import assert_no_live_call  # noqa: E402
from harness.run_case import EVAL_CACHE_DIR, _as_instant, make_case_context  # noqa: E402
from water_assistant_agent.assistant.context import ScenarioContext  # noqa: E402
from water_assistant_agent.assistant.tools.gr2l import run_roof_model  # noqa: E402
from water_assistant_agent.assistant.tools.gr2l_client import _get_client  # noqa: E402
from water_assistant_agent.assistant.tools.plot import prepare_series  # noqa: E402

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

    answered = refused = failed = mismatched = warmed = 0
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

    added = cache_keys() - before
    print(f"\n{answered} answered, {refused} refused, {failed} failed, {warmed} neighbours warmed")
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
    ``ReplayCache``, which :func:`assert_no_live_call` checks and which converts a
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
                assert_no_live_call(ctx)
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
