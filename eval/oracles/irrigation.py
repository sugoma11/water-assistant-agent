"""Oracles for family E's calculator templates (``questions.md`` §2 E).

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

T11, the predictive twin, follows in T110.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from water_assistant_agent.assistant.context import ScenarioContext
from water_assistant_agent.assistant.et_fao56 import et0_for_row
from water_assistant_agent.assistant.irrigation import run_roof
from water_assistant_agent.assistant.rules_constants import (
    REFILL_HORIZON_HOURS,
    ROOF_RULES,
    horizon_rows,
)
from water_assistant_agent.assistant.tools.gr2l_client import normalize_roof_type
from water_assistant_agent.assistant.tools.irrigation import DAY_HOURS, _measured_seed
from water_assistant_agent.assistant.tools.site import SITE_ELEVATION_M, SITE_LATITUDE

from eval.oracles.base import OracleAnswer, OracleInputError, required_params
from eval.oracles.pins import stamp


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
    roof_type = normalize_roof_type(str(roof_name))
    if roof_type not in ROOF_RULES:
        pool = ", ".join(sorted(ROOF_RULES))
        raise OracleInputError(
            f"the irrigation rule does not apply to {roof_name!r}. T07's pool is "
            f"P2 ({pool})."
        )

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

    return OracleAnswer(
        answer=run.decision.irrigate,
        unit=None,
        pins=stamp(weather_source=[weather.source]),
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
