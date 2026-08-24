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
from water_assistant_agent.assistant.irrigation import (
    Decision,
    DecisionFeatures,
    RoofRun,
    features_from_stated_values,
    irrigation_decision,
    run_roof,
)
from water_assistant_agent.assistant.rules_constants import (
    HEAT_THRESHOLD_C,
    REFILL_HORIZON_HOURS,
    ROOF_RULES,
    RoofRules,
    horizon_rows,
    rules_for,
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


# --- Family F: the rule on values the question already supplied ----------------


def _stated_decision(
    inputs: Mapping[str, Any], template: str
) -> tuple[str, RoofRules, DecisionFeatures, Decision]:
    """The ladder over four stated parameters, through the tool's pure path.

    ``features_from_stated_values`` and ``irrigation_decision`` are the two
    functions ``calc_irrigation`` calls when all three stated arguments are
    supplied, and calling them is the whole of the computation: **no database, no
    weather, no simulation, and no ET0**. That last one is not an omission. The
    stated path takes the rain at face value where the modelled path spends part
    of it on evaporation first, which is a documented difference between the two
    paths (``irrigation_tool.md`` § One ladder, two entry points) and not a step
    this oracle may add back.

    Shared by T16a and T16b because the two supply **identical inputs**
    (``questions.md`` §2 F). What separates them is the route cue and the
    must-nots, and neither is an oracle's to own.
    """
    roof_name, moisture, temperature, rain = required_params(
        inputs, "roof", "x", "tmax", "y"
    )
    roof_type = irrigable_roof(roof_name, template)
    rules = rules_for(roof_type)

    moisture, temperature, rain = float(moisture), float(temperature), float(rain)
    if not 0.0 <= moisture <= 100.0:
        raise OracleInputError(
            f"x is a percentage water content and must be between 0 and 100, got "
            f"{moisture}; the tool would answer invalid_argument."
        )
    if rain < 0.0:
        raise OracleInputError(f"y is a rain total and cannot be negative, got {rain}.")
    _off_the_rungs(rules, moisture, temperature, template)

    features = features_from_stated_values(
        rules,
        soil_moisture_pct=moisture,
        max_temperature_c=temperature,
        forecast_precip_mm=rain,
    )
    return roof_type, rules, features, irrigation_decision(features, rules)


def _off_the_rungs(
    rules: RoofRules, moisture: float, temperature: float, template: str
) -> None:
    """Refuse a stated value sitting exactly on a rung's level.

    The card states each comparison's direction — "reaches it or drops below it"
    for the wilting point, "a store above it" for the dry threshold, "stays below"
    for the heat threshold — so a tie is decided rather than ambiguous. It is
    still a case that measures whether a candidate read the tie-break convention
    off a card, where the template exists to measure whether it applied the rule
    at all, and §1.6 discards an answer sitting on its own threshold for that
    reason. Both roofs and both thresholds are checked, because the levels differ
    per roof and a value that is a tie on one is comfortably clear of the other.
    """
    ties = {
        "the wilting point": rules.wilting_pct,
        "the dry threshold": rules.dry_pct,
    }
    for name, level in ties.items():
        if moisture == level:
            raise OracleInputError(
                f"{template} states {moisture} %θ, which is exactly {name} for the "
                f"{rules.roof} roof; the answer would turn on a tie-break rather than "
                "on the rule (questions.md §1.6)."
            )
    if temperature == HEAT_THRESHOLD_C:
        raise OracleInputError(
            f"{template} states {temperature} °C, exactly the heat threshold; the "
            "answer would turn on a tie-break (questions.md §1.6)."
        )


def _stated_answer(
    roof_type: str,
    rules: RoofRules,
    features: DecisionFeatures,
    decision: Decision,
    inputs: Mapping[str, Any],
) -> OracleAnswer:
    """One stated-value decision, and enough of the ladder to hand-check it."""
    params = inputs.get("params") or {}
    return OracleAnswer(
        answer=decision.irrigate,
        unit=None,
        # `duckdb_sha256` alone, and here it really is the schema's floor: this
        # answer reads no table, no weather and no model. `pins.py`'s rule is
        # stamped-where-read, and nothing but the constants was read.
        pins=stamp(),
        detail={
            "roof": roof_type,
            "reason": decision.reason.value,
            "stated": {
                "soil_moisture_pct": float(params["x"]),
                "max_temperature_c": float(params["tmax"]),
                "forecast_precip_mm": float(params["y"]),
            },
            "wilting_pct": rules.wilting_pct,
            "dry_pct": rules.dry_pct,
            "capacity_pct": rules.capacity_pct,
            "will_reach_capacity": features.will_reach_capacity,
        },
    )


async def t16a_manual_on_stated_values(
    inputs: Mapping[str, Any], ctx: ScenarioContext
) -> OracleAnswer:
    """T16a — the values are given; what does the operations manual say?

    The docs half of the pair, and the template that carries three of train's six
    distractor slots: with soil moisture, the temperature and the forecast rain
    all supplied, a candidate that queries the database, fetches the weather or
    calls the calculator has fetched something it was handed.

    **The answer is the manual's ladder, and the manual's ladder is
    ``irrigation_decision``.** The ``irrigation_rule`` card states the rung order
    and the ``irrigation_threshold`` card the per-roof levels, and both are
    ``rendered`` — their ``values:`` blocks are held equal to
    :mod:`..rules_constants` by the drift test (T068). So reading the constants is
    reading what the cards must say, which is T06's argument and the one thing
    that keeps an oracle from agreeing with a drifted card. Between them the two
    cards state the ladder *and* the numbers, without which the docs half of the
    probe would be unanswerable by construction (``agent_architecture.md`` §3.2).

    **The documentary reference is the whole cue and it is explicit**
    (``questions.md`` §1.6): T16b asks the identical question of the calculator,
    the must-nots are symmetric, and phrasing is the only discriminator. A
    paraphrase that dropped "what does the operations manual say" would move this
    case onto T16b's gold trajectory while keeping T16a's must-nots — a defect in
    the paraphrase, not a hard case.

    Params:
        roof: any spelling ``normalize_roof_type`` accepts; pool P2.
        x: the stated soil moisture, %θ.
        tmax: the stated maximum air temperature over the next 48 h, °C.
        y: the stated rain forecast over the coming week, mm.
    """
    roof_type, rules, features, decision = _stated_decision(inputs, "T16a")
    return _stated_answer(roof_type, rules, features, decision, inputs)


async def t16b_calculator_on_stated_values(
    inputs: Mapping[str, Any], ctx: ScenarioContext
) -> OracleAnswer:
    """T16b — the same values, asked as a decision rather than as a question about docs.

    The holdout half, and the transfer target: T16a trains the docs direction and
    T16b tests that the calculator direction survives it, which T07 and T11 keep
    teaching in train. Its ``lookup_reference`` must-not is what makes the probe
    bind in both directions.

    **Its answer is T16a's, and that is the point rather than a shortcut.** The
    two questions supply identical inputs; if the oracles differed, the pair
    would be comparing two quantities instead of two routes to one. So both reach
    :func:`_stated_decision`, and what the case scores differently is the
    trajectory.

    **The stated rain is the coming week's, not the next 48 hours'**, and the
    catalog said 48 h until this packet. The value fills the refill conjunct,
    which the ladder reads over its 168 h horizon (``irrigation_tool.md``
    § The rule); the 48 h belongs to the stated *temperature*, which is read over
    the decision horizon. Both sides passed the same number under the old
    phrasing, so nothing was scored wrongly — but the question described the
    number as something the rule does not treat it as.

    Params: T16a's, exactly.
    """
    roof_type, rules, features, decision = _stated_decision(inputs, "T16b")
    return _stated_answer(roof_type, rules, features, decision, inputs)
