"""Oracles for family D — the model chains (``questions.md`` §2 D).

T09 asks whether a roof's soil moisture will fall below a threshold within the
next *h* hours, and its gold trajectory is
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

T10 and T19 follow in T110; both read the same run, so they will share this
module's call.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from water_assistant_agent.assistant.context import ScenarioContext
from water_assistant_agent.assistant.tools.gr2l import run_roof_model
from water_assistant_agent.assistant.tools.gr2l_client import (
    NON_MODELLABLE_ROOFS,
    ROOF_PRESETS,
    normalize_roof_type,
)
from water_assistant_agent.assistant.tools.weather_client import resolve_window

from eval.oracles.base import OracleAnswer, OracleInputError, required_params
from eval.oracles.pins import stamp

HOURS_PER_DAY = 24


def forecast_days_for(hours: int) -> int:
    """The whole days a "next *h* hours" question spans, counting today as day 1.

    GR2L's rows are daily, so an hourly horizon has to become a day count
    somewhere, and it becomes one *here* rather than inside the answer: 24 h is
    one day (today), 72 h is three (today and the two after it). Hours that do
    not divide into whole days are refused rather than rounded — a rounding rule
    invented in the oracle is a rule the candidate was never told, and the
    catalog samples ``h`` from whole days.
    """
    if hours <= 0 or hours % HOURS_PER_DAY:
        raise OracleInputError(
            f"h must be a positive whole number of days in hours (24, 48, 72, …), got {hours}."
        )
    return hours // HOURS_PER_DAY


async def t09_falls_below_threshold(
    inputs: Mapping[str, Any], ctx: ScenarioContext
) -> OracleAnswer:
    """T09 — will this roof's soil moisture fall below *thr* %θ within *h* hours?

    The window is resolved by ``resolve_window(forecast_days=h/24)``, layer 1's
    own resolver and the one the tool calls: ``forecast_days`` counts from today
    forward, so the window is ``as_of``'s day through ``h/24 - 1`` days after it.
    Re-deriving those two dates here would let the oracle and the tool disagree
    about whether "the next 72 hours" includes today.

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
        h: the horizon in hours, a whole number of days.
    """
    roof_name, threshold, hours = required_params(inputs, "roof", "thr", "h")
    roof_type = normalize_roof_type(str(roof_name))
    if roof_type in NON_MODELLABLE_ROOFS or roof_type not in ROOF_PRESETS:
        pool = ", ".join(sorted(set(ROOF_PRESETS) - set(NON_MODELLABLE_ROOFS)))
        raise OracleInputError(
            f"the water balance declines {roof_name!r}. T09's pool is P2 ({pool})."
        )

    window_start, window_end = resolve_window(
        forecast_days=forecast_days_for(int(hours)), today=ctx.as_of.date()
    )
    run = await run_roof_model(ctx, roof_type, window_start, window_end)

    minima = [day.swc_pct for day in run.days if day.swc_pct is not None]
    if not minima:
        raise OracleInputError(
            f"the run over {window_start}..{window_end} returned no soil-moisture "
            "series to read a minimum from."
        )
    minimum = min(minima)

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
