"""Oracles for family C — the pure weather templates (``questions.md`` §2 C).

T18a is the only one here in the pilot set, and like T17a it has no numeric
oracle: the answer is an abstention. What it asserts is the *reason* — that the
window the question names really does reach past the tool's single scope limit,
checked through :func:`~..weather_client.beyond_horizon`, which is the same
predicate the tool applies before it fetches anything.

**Nothing is fetched here and nothing may be.** The horizon check fires on the
resolved window before any client is reached, so this oracle touches neither the
station nor the Archive nor the response cache — which is what lets T18a be
declared a zero-cache-entry case (T107). An oracle that fetched first and checked
after would record an entry for a window the tool never serves.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from water_assistant_agent.assistant.context import ScenarioContext
from water_assistant_agent.assistant.tools.weather_client import (
    FORECAST_HORIZON_DAYS,
    beyond_horizon,
    resolve_window,
)

from eval.oracles.base import OracleAnswer, OracleInputError, required_params
from eval.oracles.pins import stamp


async def t18a_unservable_window(
    inputs: Mapping[str, Any], ctx: ScenarioContext
) -> OracleAnswer:
    """T18a — a well-formed forecast window beyond the 16-day horizon.

    The abstention is **tool-signalled**: the window resolves cleanly, so this is
    not an argument fault, and the tool types ``not_available`` with a reason the
    agent relays as the contract status. Keeping the two apart is what makes the
    false-abstention rate readable (``decisions.md`` § Typed abstention) — an
    ``invalid_argument`` here would be a fumble the candidate could fix, and a
    scope limit is not.

    **The horizon is read, not restated.** ``FORECAST_HORIZON_DAYS`` and
    ``beyond_horizon`` are layer 1's own, so a horizon that moves moves this
    oracle with it rather than leaving a case that asserts the old limit.

    Params:
        ahead_days: how far past ``as_of`` the question reaches. Carried as a
            count rather than as a date so T104's ``period_param_within_as_of``
            assertion does not read it as a day the case must be able to see —
            the whole point of the template is that it cannot.
    """
    (ahead_days,) = required_params(inputs, "ahead_days")
    today = ctx.as_of.date()
    target = today + timedelta(days=int(ahead_days))
    start, end = resolve_window(
        start_date=target.isoformat(), end_date=target.isoformat(), today=today
    )

    if not beyond_horizon(end, today):
        raise OracleInputError(
            f"{end} is within {FORECAST_HORIZON_DAYS} days of {today}, so the tool "
            "would serve it and the case is not an abstention at all."
        )

    return OracleAnswer(
        answer=None,
        unit=None,
        status="not_available",
        pins=stamp(),
        detail={
            "window": f"{start}..{end}",
            "horizon_days": FORECAST_HORIZON_DAYS,
            "days_past_horizon": (target - today).days - FORECAST_HORIZON_DAYS,
        },
    )
