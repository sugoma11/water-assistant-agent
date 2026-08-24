"""Oracles for family D — the model chains (``questions.md`` §2 D).

T09 asks whether a roof's soil moisture will fall below a threshold over the
next *d* days, and its gold trajectory is
``predict_green_roof_water_balance_tool`` alone. The oracle runs the same
simulation the tool runs, through ``run_roof_model`` — the shared composition
whose steps are the tool's own: ``ctx.weather`` for the forcing, ``_resolve_seed``
under ``swc.seed_bound``, ``resolve_roof_parameters`` for the presets, and
``run_gr2l`` with ``ctx.cache`` for the model itself.

**Why not call ``run_gr2l`` directly.** It takes assembled forcing rows and a
resolved ``RoofParameters``, so an oracle calling it would have to build both —
fetch the weather, apply the seed rule, convert %θ to the mm GR2L consumes, look
up the roof's preset. Every one of those is a place the oracle could come to mean
something slightly different from the tool while both kept running. Going
through ``run_roof_model`` reaches ``run_gr2l`` by the tool's own route, which is
what "oracle and tool cannot diverge" has to mean here.

**Three templates, one run.** T09 asks whether the run's minimum crosses a
threshold, T10 asks what that minimum *is*, and T19 asks how far the run sat
from the sensor record over a window that has already happened. All three read
one ``run_roof_model`` call, which is why they share :func:`modelled_run` and
:func:`min_swc_pct` rather than each composing the steps. Family G's overrides
reach the same call with `forcings`, an `albedo` or a stated seed set
(:mod:`eval.oracles.counterfactual`), so the scope check and the minimum are
imported from here rather than written twice.

**Every window here is a whole day count named in the question** — *d* days
forward for T09 and T10, *d* complete past days for T19 — and resolved by
``resolve_window``, layer 1's own. That is T107's finding rather than a
convention (:func:`forecast_days_for`), and ``decisions.md`` § Forward horizons
are counted in days is where it binds on the catalog.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import date
from typing import Any

from water_assistant_agent.assistant.context import ScenarioContext
from water_assistant_agent.assistant.tools.gr2l import (
    ModelRun,
    _compare_to_measured,
    run_roof_model,
)
from water_assistant_agent.assistant.tools.gr2l_client import (
    NON_MODELLABLE_ROOFS,
    ROOF_PRESETS,
    normalize_roof_type,
)
from water_assistant_agent.assistant.tools.swc import daily_mean_swc
from water_assistant_agent.assistant.tools.weather_client import resolve_window

from eval.oracles.base import OracleAnswer, OracleInputError, required_params
from eval.oracles.pins import stamp


def modellable_roof(name: Any, template: str) -> str:
    """*name* as the roof type GR2L models, or a refusal naming pool P2.

    The scope test is ``NON_MODELLABLE_ROOFS`` — the tool's own table, matched
    against a ``normalize_roof_type``d argument exactly as the tool matches it —
    and the presets are the second half of it, because a name in neither is an
    ``invalid_argument`` rather than a scope limit.

    Reaching here with the gravel roof or the wetland is a **sampling defect and
    not a hard case**: those two are family I's whole subject (``questions.md``
    §1.8), and an answer computed for one under a family D or G template would
    record as answerable a request every modelling route declines.
    """
    roof_type = normalize_roof_type(str(name))
    if roof_type in NON_MODELLABLE_ROOFS or roof_type not in ROOF_PRESETS:
        pool = ", ".join(sorted(set(ROOF_PRESETS) - set(NON_MODELLABLE_ROOFS)))
        raise OracleInputError(
            f"the water balance declines {name!r}. {template}'s pool is P2 ({pool})."
        )
    return roof_type


def min_swc_pct(run: ModelRun, window: str) -> float:
    """The run's driest modelled day, in %θ — the tool's ``summary.min_swc_pct``.

    One line either way, and it is written here rather than taken from
    :func:`~..tools.gr2l._summarize` because that function reduces the *whole*
    summary and needs the forcing rows back to do it, which ``run_roof_model``
    does not return. What keeps the two from drifting is a test rather than an
    import: T09's and T10's agreement checks compare this number against the
    tool's own summary over one context.

    Day 1 only seeds the stores and its ``swc_pct`` counts like any other day's:
    the question is about the roof's state over the window, and the seed *is*
    that state where the window opens.
    """
    values = [day.swc_pct for day in run.days if day.swc_pct is not None]
    if not values:
        raise OracleInputError(
            f"the run over {window} returned no soil-moisture series to read a "
            "minimum from."
        )
    return min(values)


async def modelled_run(
    ctx: ScenarioContext,
    roof_type: str,
    window: tuple[str, str],
    **overrides: Any,
) -> ModelRun:
    """One GR2L run over an already-resolved window, through the tool's own route.

    A thin pass-through to ``run_roof_model`` so that every oracle in families D
    and G reaches ``run_gr2l`` at the same call site with the same seams —
    ``ctx.weather`` for the forcing, ``ctx.db`` for the seed under
    ``swc.seed_bound``, ``ctx.cache`` for the model hop. *overrides* are the
    counterfactual arguments (``initial_soil_moisture_pct``, ``albedo``,
    ``forcings``), passed through unchanged so a family G oracle states its
    override and nothing else.
    """
    return await run_roof_model(ctx, roof_type, window[0], window[1], **overrides)


def forecast_days_for(days: Any) -> int:
    """The horizon as a positive whole number of days, counting today as day 1.

    **There is nothing to convert here, and that is the point.** T09 was phrased
    in hours until T107's pilot, which is where the cost showed: GR2L's rows are
    daily, so an hourly horizon had to become a day count somewhere, and wherever
    it happened it was a rule the candidate had never been told. The oracle read
    "the next 72 hours" as three days — today and the two after it — while a
    candidate writing the window as explicit dates read the same phrase as four,
    and the two disagreed silently on every draw whose minimum did not happen to
    sit on the same side of the threshold in both windows.

    So the catalog now names days (``questions.md`` §2 D) and the candidate passes
    the number the question names straight into ``forecast_days``. What is left to
    check is only that the draw is a whole positive count, which the generator's
    own sampling already guarantees; a non-integer reaching here is a template
    fault, not a hard case.
    """
    if isinstance(days, bool) or not isinstance(days, int) or days <= 0:
        raise OracleInputError(
            f"d must be a positive whole number of days (1, 2, 3, …), got {days!r}."
        )
    return days


def past_days_for(days: Any) -> int:
    """:func:`forecast_days_for`'s rule, for a window that reaches backwards.

    One rule and two directions rather than two rules: T19's "the last *d* days"
    hides exactly the conversion "last week" hid, and the answer to it is the
    same — the question names a whole day count and the oracle resolves that
    parameter, never the prose (``decisions.md`` § Forward horizons are counted
    in days). Delegating rather than restating is the point: a horizon rule that
    existed twice could come to mean two things.
    """
    return forecast_days_for(days)


async def t09_falls_below_threshold(
    inputs: Mapping[str, Any], ctx: ScenarioContext
) -> OracleAnswer:
    """T09 — will this roof's soil moisture fall below *thr* %θ over the next *d* days?

    The window is resolved by ``resolve_window(forecast_days=d)``, layer 1's own
    resolver and the one the tool calls: ``forecast_days`` counts from today
    forward, so the window is ``as_of``'s day through ``d - 1`` days after it.
    Re-deriving those two dates here would let the oracle and the tool disagree
    about where the window ends — and phrasing the question in days is what stops
    them disagreeing about where it *starts* (:func:`forecast_days_for`).

    The comparison is **strict** (``min < thr``), matching the question's "fall
    below". §1.6's oracle-validity filter discards a draw whose minimum sits
    within tolerance of its own threshold, so the strictness is not what decides
    a case that survives generation — but it has to be written down somewhere,
    and this is the place.

    Day 1 only seeds the stores, and its ``swc_pct`` is part of the series like
    any other day: the question is about the roof's state over the window, and
    the seed *is* the roof's state at the window's start.

    Params:
        roof: any spelling ``normalize_roof_type`` accepts; pool P2.
        thr: the threshold in %θ.
        d: the horizon in whole days, counting today as day 1.
    """
    roof_name, threshold, days = required_params(inputs, "roof", "thr", "d")
    roof_type = modellable_roof(roof_name, "T09")

    window = resolve_window(
        forecast_days=forecast_days_for(days), today=ctx.as_of.date()
    )
    window_start, window_end = window
    run = await modelled_run(ctx, roof_type, window)
    minimum = min_swc_pct(run, f"{window_start}..{window_end}")

    return OracleAnswer(
        answer=bool(minimum < float(threshold)),
        unit=None,
        pins=stamp(weather_source=[run.weather_source], used_gr2l=True),
        detail={
            "roof": roof_type,
            "threshold_pct": float(threshold),
            "min_swc_pct": minimum,
            "seed_swc_pct": run.seed.swc_pct,
            "seed_source": run.seed.source,
            "window": f"{window_start}..{window_end}",
            "days": len(run.days),
        },
    )


async def t10_predicted_minimum(
    inputs: Mapping[str, Any], ctx: ScenarioContext
) -> OracleAnswer:
    """T10 — the lowest soil moisture predicted over the next *d* days, in %θ.

    T09's number without T09's comparison, off the same run and the same
    :func:`min_swc_pct`. The pair is deliberate: a candidate that reports the
    minimum correctly and then compares it wrongly fails T09 and passes T10, and
    two oracles with two notions of "the minimum" could not tell those apart.

    **The unit is %θ and not millimetres** (``questions.md`` §1.5). GR2L holds
    the substrate store in mm and ``run_roof_model`` converts once, through the
    tool's own ``_to_days``; no template in this catalog asks for millimetres of
    storage, and answering in them would be a different quantity wearing the same
    number.

    **Asked in days, for T09's reason.** The catalog carried "over the next 72 h"
    here until this packet, which is the phrasing T107 measured a silent
    oracle/candidate disagreement on (:func:`forecast_days_for`). The horizon is
    now the number the question names.

    Params:
        roof: any spelling ``normalize_roof_type`` accepts; pool P2.
        d: the horizon in whole days, counting today as day 1.
    """
    roof_name, days = required_params(inputs, "roof", "d")
    roof_type = modellable_roof(roof_name, "T10")

    window = resolve_window(
        forecast_days=forecast_days_for(days), today=ctx.as_of.date()
    )
    window_start, window_end = window
    run = await modelled_run(ctx, roof_type, window)
    minimum = min_swc_pct(run, f"{window_start}..{window_end}")

    return OracleAnswer(
        answer=minimum,
        unit="%θ",
        pins=stamp(weather_source=[run.weather_source], used_gr2l=True),
        detail={
            "roof": roof_type,
            "min_swc_pct": minimum,
            "driest_day": min(
                (day for day in run.days if day.swc_pct is not None),
                key=lambda day: day.swc_pct,
            ).Date,
            "seed_swc_pct": run.seed.swc_pct,
            "seed_source": run.seed.source,
            "window": f"{window_start}..{window_end}",
            "days": len(run.days),
        },
    )


async def t19_model_deviation(
    inputs: Mapping[str, Any], ctx: ScenarioContext
) -> OracleAnswer:
    """T19 — how far the model sat from the sensor over the last *d* days, in pp.

    The mean |predicted − measured| that ``evaluate_against_measured`` reports,
    reached through the two functions the tool reaches it through:
    ``swc.daily_mean_swc`` for the measured series and
    ``gr2l._compare_to_measured`` for the join and the statistics. Both are read
    off ``ctx.db``, the case's as-of executor, so the comparison series is
    bounded at the same cut as the seed and a case can never be scored against
    readings taken after its own ``as_of``.

    **A deviation between two %θ states is in pp** (``questions.md`` §1.5): the
    prediction and the reading are both water contents, the gap between them is
    not, and the unit is what says so. ``MeasuredComparison`` calls the field
    ``mean_abs_deviation_pct`` because that is the pair of states it is derived
    from; the answer's unit is the derived quantity's.

    **The window is *d* complete past days ending yesterday**, which is what
    ``resolve_window(past_days=d)`` means and therefore what a candidate passing
    the number the question names gets. The catalog said "last week" until this
    packet: a calendar week beginning Monday is a different set of days from the
    seven ending yesterday, and the oracle resolves a parameter rather than
    prose (``decisions.md`` § Forward horizons are counted in days).

    **The window is past, so the must-not is fair.** The model tool fetches its
    own weather — the disclosure its contract mandates — so
    ``get_weather_forecast_tool`` is listed as a distractor here and a
    retrospective window is where that claim is checkable: nothing about last
    week needs a forecast.

    Params:
        roof: any spelling ``normalize_roof_type`` accepts; pool P2.
        d: how many complete past days the window covers, ending yesterday.
    """
    roof_name, days = required_params(inputs, "roof", "d")
    roof_type = modellable_roof(roof_name, "T19")

    window = resolve_window(past_days=past_days_for(days), today=ctx.as_of.date())
    window_start, window_end = window
    run = await modelled_run(ctx, roof_type, window)
    if not run.days:
        raise OracleInputError(
            f"the run over {window_start}..{window_end} returned no days to compare."
        )

    measured = await asyncio.to_thread(
        daily_mean_swc,
        ctx.db,
        roof_type,
        date.fromisoformat(run.days[0].Date),
        date.fromisoformat(run.days[-1].Date),
    )
    evaluation = _compare_to_measured(run.days, measured)
    if evaluation.mean_abs_deviation_pct is None:
        raise OracleInputError(
            f"the soil-moisture record covers none of {window_start}..{window_end} for "
            f"{roof_type}, so there is nothing to compare the run against; the coverage "
            "filter should have rejected this draw (questions.md §1.6)."
        )

    return OracleAnswer(
        answer=evaluation.mean_abs_deviation_pct,
        unit="pp",
        pins=stamp(weather_source=[run.weather_source], used_gr2l=True),
        detail={
            "roof": roof_type,
            "window": f"{window_start}..{window_end}",
            "compared_days": evaluation.days,
            "overlap": f"{evaluation.overlap_start}..{evaluation.overlap_end}",
            "max_abs_deviation_pct": evaluation.max_abs_deviation_pct,
            "seed_swc_pct": run.seed.swc_pct,
            "seed_source": run.seed.source,
        },
    )
