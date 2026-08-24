"""Oracles for the irrigation decision — family E's calculator templates and
family F's given-values controls (``questions.md`` §2 E and F).

T07 asks whether a roof needs irrigation *now*, and its gold trajectory is
``calc_irrigation`` alone. The oracle therefore has to reach the same boolean the
tool reaches, which means walking the same ladder over the same features — so it
calls ``run_roof``, whose final step *is* ``irrigation_decision``
(``assistant/irrigation.py``), rather than re-implementing the comparison chain.
A rung reordered in the ladder moves the tool and this oracle in the same commit.

**T07's phrasing was settled in T009 and this oracle matches the settled route.**
"According to the operations manual" is deleted rather than reworded: it named
the documentation against a ``calc_irrigation`` gold set, which is §1.6's route
cue pointing the other way. So no card is read here and none is expected —
``Cards: []`` in the catalog — and an oracle that consulted a card would be
encoding the route the convention rejects.

**Two entry points, four templates, one ladder.** The tool's modelled path
answers T07 and T11 and its stated path answers T16a and T16b, and the split
here is the tool's own rather than a family boundary: T16a's gold route is the
*documentation* and T16b's is the calculator, but the two questions supply
identical values (``questions.md`` §2 F) and the manual's ladder is
``irrigation_decision`` — the cards' ``values:`` blocks are test-bound to
``rules_constants`` (T068), so reading the constants is reading what the cards
must say, which is T06's argument. What separates T16a from T16b is the route
cue and the must-nots, both of which are per-template constants copied in at
generation, and neither of which an oracle owns.

**Nothing here reaches the network, on any of the four.** The bucket, the ladder
and ET0 are local Python, so an irrigation case costs no cache entry and has no
``upstream`` error class (``agent_architecture.md`` §3.5). The stated path goes
further and touches nothing at all — no database, no weather, no simulation —
which is why family F's oracles fetch no ET0: :func:`features_from_stated_values`
takes none, and computing one here would add a step the tool does not take.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from water_assistant_agent.assistant.context import ScenarioContext
from water_assistant_agent.assistant.et_fao56 import et0_for_row
from water_assistant_agent.assistant.irrigation import RoofRun, run_roof
from water_assistant_agent.assistant.rules_constants import (
    REFILL_HORIZON_HOURS,
    ROOF_RULES,
    horizon_rows,
)
from water_assistant_agent.assistant.tools.gr2l_client import normalize_roof_type
from water_assistant_agent.assistant.tools.irrigation import DAY_HOURS, _measured_seed
from water_assistant_agent.assistant.tools.schemas import SwcSeed
from water_assistant_agent.assistant.tools.site import SITE_ELEVATION_M, SITE_LATITUDE

from eval.oracles.base import OracleAnswer, OracleInputError, required_params
from eval.oracles.pins import stamp


def irrigable_roof(name: Any, template: str) -> str:
    """*name* as a roof the rule applies to, or a refusal naming pool P2.

    ``ROOF_RULES`` is the rule's own membership and the absence of the gravel
    roof and the wetland from it is the same scope statement
    ``NON_MODELLABLE_ROOFS`` makes (``rules_constants``). Reaching here with
    either is a sampling defect rather than a hard case: those two are family I's
    subject, and answering a decision for one would record as answerable a
    request the calculator declines.
    """
    roof_type = normalize_roof_type(str(name))
    if roof_type not in ROOF_RULES:
        pool = ", ".join(sorted(ROOF_RULES))
        raise OracleInputError(
            f"the irrigation rule does not apply to {name!r}. {template}'s pool is "
            f"P2 ({pool})."
        )
    return roof_type


async def _decide_from_the_roofs_own_data(
    roof_type: str, ctx: ScenarioContext
) -> tuple[RoofRun, SwcSeed, str]:
    """The tool's modelled path, step for step: ``(run, seed, weather_source)``.

    Shared by T07 and T11 because the tool takes **no date arguments** — the
    decision is about the roof's coming week whichever tense the question is
    asked in — so the two templates reach one call and differ in the question
    rather than in the computation (:func:`t11_needs_irrigation_tomorrow`).
    """
    today = ctx.as_of.date()
    window_end = today + timedelta(
        days=horizon_rows(REFILL_HORIZON_HOURS, step_hours=DAY_HOURS) - 1
    )
    weather = await ctx.weather.fetch(
        start_date=today.isoformat(), end_date=window_end.isoformat()
    )
    if not weather.data:
        raise OracleInputError(
            f"no forecast days were returned for {today}..{window_end}, so the roof's "
            "moisture cannot be projected."
        )

    seed = await _measured_seed(ctx.db, roof_type, today, as_of=ctx.as_of)
    run = run_roof(
        roof_type,
        precipitation_mm=[row.precip for row in weather.data],
        et0_mm=[
            et0_for_row(row, latitude=SITE_LATITUDE, elevation_m=SITE_ELEVATION_M)
            for row in weather.data
        ],
        temperature_c=[row.tx for row in weather.data],
        seed_theta_pct=seed.swc_pct,
        step_hours=DAY_HOURS,
    )
    return run, seed, weather.source


def _decision_answer(
    run: RoofRun, seed: SwcSeed, source: str, roof_type: str, ctx: ScenarioContext
) -> OracleAnswer:
    """One modelled decision, projected onto the four keys an oracle owns."""
    today = ctx.as_of.date()
    window_end = today + timedelta(
        days=horizon_rows(REFILL_HORIZON_HOURS, step_hours=DAY_HOURS) - 1
    )
    return OracleAnswer(
        answer=run.decision.irrigate,
        unit=None,
        pins=stamp(weather_source=[source]),
        detail={
            "roof": roof_type,
            "reason": run.decision.reason.value,
            "seed_swc_pct": seed.swc_pct,
            "seed_measured_at": seed.measured_at,
            "min_store": run.features.min_store,
            "max_temperature_c": run.features.max_temperature_c,
            "will_reach_capacity": run.features.will_reach_capacity,
            "window": f"{today}..{window_end}",
        },
    )


async def t07_needs_irrigation_now(
    inputs: Mapping[str, Any], ctx: ScenarioContext
) -> OracleAnswer:
    """T07 — does this roof need irrigating right now?

    The tool's modelled path, step for step and through the same functions:

    1. the window is the **refill horizon from today**, because the decision is
       about now and the rule looks a week ahead for rain — the tool takes no
       date arguments for exactly this reason;
    2. the forcing comes from ``ctx.weather``, so oracle and rollout are forced
       by the same rows from the same source;
    3. the seed is ``_measured_seed`` — the tool's own function, and through it
       ``swc.seed_bound``'s one rule. Its leading underscore is deliberate and
       so is importing it anyway: it is private to the *agent-facing* surface,
       and reaching past that is what an oracle does (principle 1). Calling
       ``latest_measured_swc`` directly instead would leave this module holding
       its own copy of a rounding (``round(θ, 2)``) the decision can turn on;
    4. ET0 is ``et0_for_row`` at the surveyed site height and the reference
       crop's albedo, and the temperature forcing is each day's ``tx`` — a
       measured daily maximum, the stated deviation in ``irrigation_tool.md``
       § Horizons and step;
    5. ``run_roof`` buckets, windows and decides, ending in
       ``irrigation_decision``.

    The 48 h in the catalog's "measured seed plus a 48 h lookahead" is the
    *decision* horizon, applied inside ``summarize``; the fetched week is the
    *refill* horizon the fourth rung reads. Both are ``rules_constants``' and
    neither is restated here.

    Params:
        roof: any spelling ``normalize_roof_type`` accepts; pool P2.
    """
    (roof_name,) = required_params(inputs, "roof")
    roof_type = irrigable_roof(roof_name, "T07")
    run, seed, source = await _decide_from_the_roofs_own_data(roof_type, ctx)
    return _decision_answer(run, seed, source, roof_type, ctx)


async def t11_needs_irrigation_tomorrow(
    inputs: Mapping[str, Any], ctx: ScenarioContext
) -> OracleAnswer:
    """T11 — does this roof need irrigating tomorrow? T07's call, and deliberately so.

    **The twin is in the question, not in the computation, and that is forced by
    the tool rather than chosen here.** ``calc_irrigation`` takes no date
    arguments: the decision is about the roof's coming week, the decision horizon
    is 48 h — today and tomorrow — and the refill horizon is the week after. So a
    candidate asked "tomorrow?" issues the same call as one asked "right now?",
    and there is exactly one boolean either can be scored against. An oracle that
    computed a *different* number for T11 — shifting the window forward a day,
    say, or reading only tomorrow's store — would encode as truth a number no
    gold route produces, which is the failure ``base.py`` says an oracle exists
    to prevent.

    What the pair does measure is real and is not the answer: T11 is the
    predictive phrasing over the same gold set, so it probes whether a candidate
    reaches the calculator for a question about tomorrow instead of reaching for
    the water-balance model or the database. The catalog's "the calculator's own
    modelled features" is that path, and it is the one below.

    **Its "no" is not T07's "no" case by case**, because the two draw
    independently: a roof and an ``as_of`` are sampled per instance, and the
    per-param disjointness rule keeps train and test_seen apart on both. Two
    instances that happened to share a roof and a day would share an answer, and
    would be the same question asked twice — which is what a paraphrase already
    is.

    Params:
        roof: any spelling ``normalize_roof_type`` accepts; pool P2.
    """
    (roof_name,) = required_params(inputs, "roof")
    roof_type = irrigable_roof(roof_name, "T11")
    run, seed, source = await _decide_from_the_roofs_own_data(roof_type, ctx)
    return _decision_answer(run, seed, source, roof_type, ctx)
