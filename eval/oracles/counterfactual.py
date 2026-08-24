"""Oracles for family G — the counterfactuals (``questions.md`` §2 G).

Four templates, one call, three override arguments. T21 forces the weather, T22
replaces a physical parameter, T23 states the day-1 state and T26 composes them.
Every one of them reaches ``run_roof_model`` through
:func:`~eval.oracles.model_chain.modelled_run`, which is the tool's own route —
so the override arrives at ``run_gr2l`` the way the tool would deliver it, and
the counterfactual is applied to the **forcing** rather than to the result. That
last property is the family's whole subject: a wrapper adjusting a baseline
afterwards would produce a plausible number the model never computed, and it
would agree with this oracle if the oracle adjusted a baseline too.

**What the scoring reads is not what these oracles return.** The counterfactual
families are scored on the *presence and plausibility* of ``forcings`` /
``albedo`` / ``initial_soil_moisture_pct`` in the call's arguments, never on the
tool result (``decisions.md`` § GR2L argument surface). The number below is still
the answer metric's, and it is the reason the argument check can be loose: a
candidate that names the right argument and then reports a number the model did
not produce fails on the answer while passing on the trajectory, and the two
metrics are supposed to be able to disagree.

**Every override is stated in the tool's own vocabulary and never translated.**
``forcings`` is keyed by ``DailyWeatherRow``'s field names — ``precip``, not
``precip_mm`` — and ``normalize_forcings`` is what validates it, reached through
``run_roof_model`` rather than reimplemented. A second vocabulary here would be
the translation table ``decisions.md`` rejects, in the one place it could not be
noticed.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any

from water_assistant_agent.assistant.context import ScenarioContext
from water_assistant_agent.assistant.knowledge.store import load_card
from water_assistant_agent.assistant.rules_constants import rules_for
from water_assistant_agent.assistant.tools.gr2l import FORCEABLE_FIELDS
from water_assistant_agent.assistant.tools.gr2l_client import ROOF_PRESETS
from water_assistant_agent.assistant.tools.swc import mm_to_theta_pct
from water_assistant_agent.assistant.tools.weather_client import (
    FORECAST_HORIZON_DAYS,
    beyond_horizon,
    resolve_window,
)

from eval.oracles.base import OracleAnswer, OracleInputError, required_params
from eval.oracles.model_chain import (
    forecast_days_for,
    min_swc_pct,
    modellable_roof,
    modelled_run,
)
from eval.oracles.pins import stamp
from eval.oracles.reference import THRESHOLD_CARD

PRECIP = "precip"
"""The forced field, and it is read off the row model rather than written here.

:data:`~..tools.gr2l.FORCEABLE_FIELDS` is derived from ``DailyWeatherRow``, so
this constant is checked against it at every call site
(:func:`rain_forcing`) instead of being a second place a field rename could go
wrong silently.
"""


def rain_forcing(day: date, millimetres: float) -> dict[str, dict[str, float]]:
    """``{"precip": {day: mm}}`` — the sparse overlay T21 and T26 apply.

    Sparse is the contract, not an optimization: every day the caller does not
    name keeps its fetched value, and every field it does not name keeps its own.
    So a one-day downpour is one entry, and the rest of the window stays the
    weather that was actually forecast.
    """
    if PRECIP not in FORCEABLE_FIELDS:
        raise OracleInputError(
            f"{PRECIP!r} is no longer a field a forcing may override; the row model "
            f"now carries {', '.join(FORCEABLE_FIELDS)}."
        )
    if millimetres < 0.0:
        raise OracleInputError(
            f"a rain forcing cannot be negative, got {millimetres} mm."
        )
    return {PRECIP: {day.isoformat(): float(millimetres)}}


def forward_window(days: Any, ctx: ScenarioContext) -> tuple[str, str]:
    """*days* forward from ``as_of``, checked against the tool's own horizon.

    A counterfactual past the 16-day limit is T18a's abstention wearing family
    G's words: the tool types ``not_available`` and no number exists to be
    scored, so the draw is refused rather than answered.
    """
    today = ctx.as_of.date()
    start, end = resolve_window(forecast_days=forecast_days_for(days), today=today)
    if beyond_horizon(end, today):
        raise OracleInputError(
            f"a {days}-day window ends {end}, past the tool's {FORECAST_HORIZON_DAYS}-day "
            "limit; the tool would type not_available and there is no number to score."
        )
    return start, end


class InertOverrideError(OracleInputError):
    """An override the model demonstrably ignores, so the answer is the baseline.

    A subclass rather than a bare :class:`OracleInputError` because the two are
    read differently. An ordinary oracle refusal says *this draw* was badly
    sampled and the generator should resample. This one says the answer does not
    depend on the argument the template exists to probe, which a resample cannot
    fix if the cause is the model rather than the draw — and writing the case
    anyway would put a number in the gold set that a candidate ignoring the
    override earns in full.

    Both causes occur here and they are told apart by which template raises. T23
    hits it on a draw whose window is long or wet enough that the store saturates
    and forgets its own initial condition; T22 hits it on every draw, because the
    served GR2L accepts ``albedo`` and does nothing with it (``findings.md``).
    """


def forced_day_inside(window: tuple[str, str], day: date, described: str) -> date:
    """Assert the forced day is one the run covers, before any fetch.

    ``normalize_forcings`` raises the same way and this repeats it deliberately:
    reached through ``run_roof_model`` the failure is a ``ForcingError`` several
    frames in and after a weather fetch, and a template whose forced day falls
    outside its own window is a generation fault that should name itself.
    """
    start, end = (date.fromisoformat(value) for value in window)
    if not start <= day <= end:
        raise OracleInputError(
            f"{described} forces {day}, which is outside the window "
            f"{start}..{end} the question asks about; the override would silently "
            "reach no row."
        )
    return day


def _day_swc(run: Any, day: str, window: tuple[str, str]) -> float:
    """The run's modelled %θ on *day*, or a refusal naming the window it is not in."""
    value = next(
        (row.swc_pct for row in run.days if row.Date == day and row.swc_pct is not None),
        None,
    )
    if value is None:
        raise OracleInputError(
            f"the run over {window[0]}..{window[1]} returned no soil moisture for "
            f"{day}, which is the day the question asks about."
        )
    return value


async def t21_forced_rain_minimum(
    inputs: Mapping[str, Any], ctx: ScenarioContext
) -> OracleAnswer:
    """T21 — with *mm* of rain forced on one day, the window's lowest soil moisture.

    The override is applied to the **forcing**: ``run_roof_model`` overlays the
    named day's ``precip`` on the fetched rows and hands the model the result, so
    GR2L computes the counterfactual. T115 measured that this is what actually
    happens rather than a wrapper adjusting an answer — 50 mm forced on one day
    moved that day from 2.28 to 15.62 %θ and produced 36.34 mm of runoff — and
    the property is what the family exists to probe.

    **A free extra weather call is a valid route** (``questions.md`` §2 G):
    fetch-then-substitute reaches the same place, so the trajectory is not
    scored against the absence of a fetch. What is scored is that ``forcings``
    was present and plausible.

    Params:
        roof: any spelling ``normalize_roof_type`` accepts; pool P2.
        d: the horizon in whole days, counting today as day 1.
        mm: the rain to force, in millimetres.
        offset: which day of the window is forced, counting today as 0. Written
            as an offset rather than as a date so the parameter cannot name a day
            outside a window sampled independently of it.
    """
    roof_name, days, millimetres, offset = required_params(
        inputs, "roof", "d", "mm", "offset"
    )
    roof_type = modellable_roof(roof_name, "T21")
    window = forward_window(days, ctx)
    forced = forced_day_inside(
        window, ctx.as_of.date() + timedelta(days=int(offset)), "T21"
    )
    forcings = rain_forcing(forced, float(millimetres))

    run = await modelled_run(ctx, roof_type, window, forcings=forcings)
    minimum = min_swc_pct(run, f"{window[0]}..{window[1]}")

    return OracleAnswer(
        answer=minimum,
        unit="%θ",
        pins=stamp(weather_source=[run.weather_source], used_gr2l=True),
        detail={
            "roof": roof_type,
            "window": f"{window[0]}..{window[1]}",
            "forcings": run.forcings,
            "forced_day": forced.isoformat(),
            "min_swc_pct": minimum,
            "seed_swc_pct": run.seed.swc_pct,
        },
    )


async def t22_albedo_override(
    inputs: Mapping[str, Any], ctx: ScenarioContext
) -> OracleAnswer:
    """T22 — the soil moisture predicted for tomorrow with the albedo set to *a*.

    ``albedo`` is the one preset value a caller may replace — every other GR2L
    parameter is a physical property of the installed roof — and it enters
    through ``resolve_roof_parameters``, which ``run_roof_model`` calls. So the
    override reaches the model as a parameter rather than as an adjustment, on
    T21's terms.

    **The answer is tomorrow's day row, not the window's minimum**, which is what
    separates this from T21 and T23 more than the argument does. The window still
    opens today, because day 1 seeds the stores and a one-day window would be a
    simulation of nothing.

    A higher albedo reflects more energy away, which lowers evapotranspiration
    and leaves the roof wetter; the direction is monotone in the physics and is
    what would make a sampled *a* interpretable.

    **Against the GR2L build serving this deployment it is not interpretable at
    all, and the oracle refuses rather than emitting the baseline.** The service
    accepts ``albedo`` and ignores it: six values from 0.0 to 1.0 return one
    byte-identical response, where the same FAO-56 core running locally spans
    10.03 mm to 2.83 mm of ET0 over that range (``findings.md``). So every T22
    answer would equal the un-overridden prediction, and a candidate that never
    passed the argument would score full marks on the answer metric — which is
    the one failure a counterfactual template cannot tolerate. The refusal is
    :class:`InertOverrideError` and it is **measured per draw rather than
    assumed**: the oracle runs the override against the roof's own default and
    compares, so the day the service wires the parameter up, T22 begins
    materializing with no change here.

    What survives meanwhile is the half the schema actually scores: ``albedo``'s
    presence and plausibility in the call's arguments
    (``decisions.md`` § GR2L argument surface). That is a trajectory claim, and
    it does not need an answer.

    Params:
        roof: any spelling ``normalize_roof_type`` accepts; pool P2.
        a: the albedo, in [0, 1].
    """
    roof_name, albedo = required_params(inputs, "roof", "a")
    roof_type = modellable_roof(roof_name, "T22")
    albedo = float(albedo)
    default = float(ROOF_PRESETS[roof_type]["albedo"])
    if not 0.0 <= albedo <= 1.0:
        raise OracleInputError(
            f"albedo must be between 0.0 and 1.0, got {albedo}; the tool would answer "
            "invalid_argument."
        )
    if albedo == default:
        raise OracleInputError(
            f"a = {albedo} is the {roof_type} roof's own default albedo, so the "
            "counterfactual is the baseline and the case probes nothing."
        )

    window = forward_window(2, ctx)
    tomorrow = (ctx.as_of.date() + timedelta(days=1)).isoformat()
    run = await modelled_run(ctx, roof_type, window, albedo=albedo)
    predicted = _day_swc(run, tomorrow, window)

    baseline = await modelled_run(ctx, roof_type, window)
    if predicted == _day_swc(baseline, tomorrow, window):
        raise InertOverrideError(
            f"the model predicts {predicted} %θ for {tomorrow} at albedo {albedo} and "
            f"the same value at the roof's default {default}, so this case's answer "
            "does not depend on the argument T22 exists to probe. The served GR2L "
            "accepts albedo and ignores it (findings.md)."
        )

    return OracleAnswer(
        answer=predicted,
        unit="%θ",
        pins=stamp(weather_source=[run.weather_source], used_gr2l=True),
        detail={
            "roof": roof_type,
            "window": f"{window[0]}..{window[1]}",
            "albedo": run.parameters.albedo,
            "default_albedo": default,
            "day": tomorrow,
            "swc_pct": predicted,
            "seed_swc_pct": run.seed.swc_pct,
        },
    )


async def t23_state_override(
    inputs: Mapping[str, Any], ctx: ScenarioContext
) -> OracleAnswer:
    """T23 — had the roof started at *x* %θ *d* days ago, where would it be now?

    The window runs from the counterfactual day to today, and
    ``initial_soil_moisture_pct`` replaces the seed the sensor would have
    supplied. ``_resolve_seed`` takes the caller's value when it is given — the
    branch is the tool's, so the override is a seed and not a correction applied
    to one.

    **The must-not on ``get_weather_forecast_tool`` binds because the model
    fetches its own forcing**, and the window is retrospective on all but its
    last day, so there is nothing a forecast could add.

    **The answer is the last day's state**, which is what "where would it be now"
    names: the window's minimum would be a different question and is T21's.

    **The window's start is a day count, not "last Monday".** A named weekday
    denotes a different date depending on when it is read, which is the
    redenotation ``decisions.md`` § Forward horizons are counted in days forbids
    in the forward direction and forbids here for the same reason.

    **A window whose store saturates has forgotten its own seed, and that draw is
    refused.** GR2L's memory of an initial condition is finite and how finite
    depends on the weather: over a dry August week the two ends of a 5→20 %θ
    sweep converge to one value by day 7, and at ``as_of`` 2026-04-20 — a wet
    week — by **day 2**, while a drier October window still separates them by
    6.29 pp at ten days (``findings.md``). So the horizon is not a free
    parameter: past the memory, "where would it be now" has the same answer
    whatever the roof started at, and a candidate that ignored
    ``initial_soil_moisture_pct`` would score full marks. The oracle measures it
    per draw with a probe run rather than assuming a safe *d*, and raises
    :class:`InertOverrideError`.

    Params:
        roof: any spelling ``normalize_roof_type`` accepts; pool P2.
        x: the counterfactual day-1 soil moisture, %θ.
        d: how many days before today the window opens.
    """
    roof_name, moisture, days = required_params(inputs, "roof", "x", "d")
    roof_type = modellable_roof(roof_name, "T23")
    moisture = float(moisture)
    if not 0.0 <= moisture <= 100.0:
        raise OracleInputError(
            "initial_soil_moisture_pct is a percentage water content and must be "
            f"between 0 and 100, got {moisture}; the tool would answer invalid_argument."
        )

    today = ctx.as_of.date()
    start = today - timedelta(days=forecast_days_for(days))
    window = (start.isoformat(), today.isoformat())
    run = await modelled_run(
        ctx, roof_type, window, initial_soil_moisture_pct=moisture
    )
    if not run.days or run.days[-1].swc_pct is None:
        raise OracleInputError(
            f"the run over {window[0]}..{window[1]} returned no soil moisture for its "
            "last day, which is the day the question asks about."
        )
    answer = run.days[-1].swc_pct

    # The probe sits at the other end of the plausible range from the stated
    # value, so a window that still separates *these* two separates any pair the
    # template could draw. It is the roof's own store that is being probed, so
    # the bounds are the model's: Ssubmin and Ssubmax restated in %θ.
    probe = _distant_seed(moisture, roof_type)
    elsewhere = await modelled_run(
        ctx, roof_type, window, initial_soil_moisture_pct=probe
    )
    if elsewhere.days and elsewhere.days[-1].swc_pct == answer:
        raise InertOverrideError(
            f"over {window[0]}..{window[1]} the {roof_type} roof ends at {answer} %θ "
            f"whether it started at {moisture} %θ or at {probe} %θ, so the store has "
            "saturated and forgotten its initial condition. The answer does not depend "
            "on the override T23 exists to probe; draw a shorter or drier window."
        )

    return OracleAnswer(
        answer=answer,
        unit="%θ",
        pins=stamp(weather_source=[run.weather_source], used_gr2l=True),
        detail={
            "roof": roof_type,
            "window": f"{window[0]}..{window[1]}",
            "stated_seed_swc_pct": moisture,
            "seed_source": run.seed.source,
            "day": run.days[-1].Date,
            "swc_pct": answer,
            "probe_seed_swc_pct": probe,
            "probe_swc_pct": elsewhere.days[-1].swc_pct if elsewhere.days else None,
        },
    )


def _distant_seed(moisture: float, roof_type: str) -> float:
    """A second seed far from *moisture*, inside the roof's own storage range.

    Whichever of the roof's two extremes is further from the stated value, so the
    probe is the strongest test the roof admits: a window that still tells these
    two apart tells any pair apart. The extremes are ``Ssubmin`` and ``Ssubmax``
    restated in %θ, which is the range GR2L's store actually moves in — a probe
    outside it would be clipped and would report a false loss of memory.
    """
    preset = ROOF_PRESETS[roof_type]
    height = float(preset["SH"])
    low = mm_to_theta_pct(float(preset["Ssubmin"]), height)
    high = mm_to_theta_pct(float(preset["Ssubmax"]), height)
    return round(low if abs(moisture - low) > abs(moisture - high) else high, 2)


VARIANT_ALBEDO_AND_RAIN = "albedo_and_rain"
VARIANT_RAIN_CROSS_ROOF = "rain_cross_roof"
VARIANT_TRAIN_TAUGHT = "train_taught"

T26_VARIANTS: tuple[str, ...] = (
    VARIANT_ALBEDO_AND_RAIN,
    VARIANT_RAIN_CROSS_ROOF,
    VARIANT_TRAIN_TAUGHT,
)
"""T26's three shapes, ``questions.md`` §2 G's (i), (ii) and (iii).

A **shared** axis like T24a's (§1.7): all three sit in the holdout together,
because they are distinct compositions rather than values of one quantity.
"""


async def t26_composed_override(
    inputs: Mapping[str, Any], ctx: ScenarioContext
) -> OracleAnswer:
    """T26 — two overrides at once, or one override across two roofs.

    The compositional-generalization headline, and its three variants compose
    different axes:

    * **(i)** ``albedo`` **and** ``forcings`` on one roof, answered as whether
      the roof stays above its irrigation threshold — which adds
      ``lookup_reference`` to the trajectory and ``irrigation_threshold`` to the
      cards. It composes ``albedo``, whose axis is itself holdout, so it is
      interpretable only where T22 passes and is reported conditionally — and
      against the current service build T22 never passes, because the model
      ignores the parameter (:func:`t22_albedo_override`). So (i) is **still
      answerable and half inert**: the rain moves the answer and the albedo
      cannot, which makes it a composition of one live axis with one dead one.
      It is not refused here — the case does probe composing a forcing with a
      card lookup — but the ``albedo`` half of its claim is not measurable today,
      and (iii) is the variant the headline should rest on meanwhile.
    * **(ii)** ``forcings`` plus a cross-roof comparison: which of two roofs ends
      the window wetter under the same forced rain.
    * **(iii)** the same comparison with no ``albedo`` anywhere, so the headline
      does not rest on double transfer. Its parents — T21's forcings and
      T09/T10's model chain — are all train-taught.

    **The threshold on (i) is the dry rung, not the wilting point**, for T06's
    reason: the question asks whether the roof stays above the level irrigation
    is triggered at, and the wilting rung waters whatever the weather is doing.
    The card is read for the ground and the constant for the number, which is
    :mod:`eval.oracles.reference`'s rule and not a second one.

    **Both roofs run the same window with the same forcing on the comparison
    variants**, so what the answer compares is the roofs and nothing else. A tie
    is refused rather than broken: two roofs ending the window at the same %θ
    give the question two defensible answers (§1.6).

    Params:
        variant: one of :data:`T26_VARIANTS`.
        roof: the roof, on (i).
        roof_a, roof_b: the pair, on (ii) and (iii).
        a: the albedo, on (i).
        mm: the rain to force, on every variant.
        offset: which day of the window is forced, counting today as 0.
        d: the horizon in whole days.
    """
    variant, millimetres, offset, days = required_params(
        inputs, "variant", "mm", "offset", "d"
    )
    variant = str(variant)
    if variant not in T26_VARIANTS:
        raise OracleInputError(
            f"unknown T26 variant {variant!r}. Valid: {', '.join(T26_VARIANTS)}."
        )

    params = inputs.get("params") or {}
    window = forward_window(days, ctx)
    forced = forced_day_inside(
        window, ctx.as_of.date() + timedelta(days=int(offset)), f"T26({variant})"
    )
    forcings = rain_forcing(forced, float(millimetres))

    if variant == VARIANT_ALBEDO_AND_RAIN:
        return await _t26_albedo_and_rain(inputs, ctx, window, forcings, forced)
    return await _t26_cross_roof(params, ctx, window, forcings, forced, variant)


async def _t26_albedo_and_rain(
    inputs: Mapping[str, Any],
    ctx: ScenarioContext,
    window: tuple[str, str],
    forcings: dict[str, dict[str, float]],
    forced: date,
) -> OracleAnswer:
    """Variant (i): both overrides on one roof, answered against the dry threshold."""
    roof_name, albedo = required_params(inputs, "roof", "a")
    roof_type = modellable_roof(roof_name, "T26(i)")
    albedo = float(albedo)
    if not 0.0 <= albedo <= 1.0:
        raise OracleInputError(f"albedo must be between 0.0 and 1.0, got {albedo}.")

    card = load_card(THRESHOLD_CARD)
    stated = (card.values or {}).get(roof_type, {}).get("dry_pct")
    threshold = rules_for(roof_type).dry_pct
    if stated != threshold:
        raise OracleInputError(
            f"the {THRESHOLD_CARD} card states a dry threshold of {stated!r} for "
            f"{roof_type} where the constants say {threshold}; the gold route reads "
            "the card and this oracle reads the constants."
        )

    run = await modelled_run(ctx, roof_type, window, albedo=albedo, forcings=forcings)
    minimum = min_swc_pct(run, f"{window[0]}..{window[1]}")

    return OracleAnswer(
        answer=bool(minimum > threshold),
        unit=None,
        pins=stamp(weather_source=[run.weather_source], used_gr2l=True),
        detail={
            "variant": VARIANT_ALBEDO_AND_RAIN,
            "roof": roof_type,
            "window": f"{window[0]}..{window[1]}",
            "albedo": run.parameters.albedo,
            "forcings": run.forcings,
            "forced_day": forced.isoformat(),
            "min_swc_pct": minimum,
            "dry_threshold_pct": threshold,
        },
    )


async def _t26_cross_roof(
    params: Mapping[str, Any],
    ctx: ScenarioContext,
    window: tuple[str, str],
    forcings: dict[str, dict[str, float]],
    forced: date,
    variant: str,
) -> OracleAnswer:
    """Variants (ii) and (iii): one forcing, two roofs, and which ends wetter.

    Two runs over one window with one overlay, so the only thing that differs
    between them is the roof — its presets, its substrate depth and its own
    measured seed. Answered as the winning roof's canonical name rather than as a
    difference, because the question names roofs and a signed gap would need a
    convention about which way round it is written.
    """
    names = [params.get("roof_a"), params.get("roof_b")]
    if any(name is None for name in names):
        raise OracleInputError(
            f"T26({variant}) compares two roofs and needs roof_a and roof_b."
        )
    roofs = [modellable_roof(name, f"T26({variant})") for name in names]
    if roofs[0] == roofs[1]:
        raise OracleInputError(
            f"roof_a={names[0]!r} and roof_b={names[1]!r} are both {roofs[0]}; the "
            "question compares two roofs."
        )

    finals: dict[str, float] = {}
    sources: list[str] = []
    for roof_type in roofs:
        run = await modelled_run(ctx, roof_type, window, forcings=forcings)
        if not run.days or run.days[-1].swc_pct is None:
            raise OracleInputError(
                f"the {roof_type} run over {window[0]}..{window[1]} returned no soil "
                "moisture for its last day."
            )
        finals[roof_type] = run.days[-1].swc_pct
        sources.append(run.weather_source)

    if finals[roofs[0]] == finals[roofs[1]]:
        raise OracleInputError(
            f"both roofs end {window[1]} at {finals[roofs[0]]} %θ, so "
            "'which is wetter' has two defensible answers (questions.md §1.6)."
        )
    wetter = max(finals, key=lambda roof: finals[roof])

    return OracleAnswer(
        answer=wetter,
        unit=None,
        pins=stamp(weather_source=sources, used_gr2l=True),
        detail={
            "variant": variant,
            "roofs": roofs,
            "window": f"{window[0]}..{window[1]}",
            "forcings": forcings,
            "forced_day": forced.isoformat(),
            "final_swc_pct": finals,
        },
    )
