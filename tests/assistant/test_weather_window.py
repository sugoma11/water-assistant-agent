"""Weather-window resolution and the forecast horizon (T040, T041).

Two halves of one question — what window the tool accepts, and against what.
``resolve_window`` decides only whether a window is well *formed*; how far it
reaches is the wrapper's, checked against ``ctx.as_of`` and answered with the
tool's single typed ``not_available``.

The bug the resolution tests pin: Open-Meteo defaults ``forecast_days`` to 7, so
forwarding a bare ``past_days`` returned the requested history *plus* a week of
forecast, unlabelled in the transposed rows. Resolution happens in the tool
wrappers now, so the client only ever sends an explicit window.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from water_assistant_agent.assistant.context import ScenarioContext
from water_assistant_agent.assistant.tools import weather as weather_module
from water_assistant_agent.assistant.tools.schemas import (
    DailyWeatherRow,
    WeatherResult,
)
from water_assistant_agent.assistant.tools.weather_client import (
    FORECAST_HORIZON_DAYS,
    InvalidWindowError,
    resolve_window,
)

TODAY = date(2026, 7, 29)
BERLIN = ZoneInfo("Europe/Berlin")
AS_OF = datetime(2026, 3, 15, 11, 0, tzinfo=BERLIN)


def test_past_days_stops_at_yesterday() -> None:
    """The whole point: no forecast tail on a past-only window."""
    assert resolve_window(past_days=5, today=TODAY) == ("2026-07-24", "2026-07-28")


def test_forecast_days_counts_from_today() -> None:
    assert resolve_window(forecast_days=3, today=TODAY) == ("2026-07-29", "2026-07-31")


def test_both_counts_span_today() -> None:
    assert resolve_window(past_days=5, forecast_days=3, today=TODAY) == (
        "2026-07-24",
        "2026-07-31",
    )


def test_bare_call_keeps_the_seven_day_default() -> None:
    assert resolve_window(today=TODAY) == ("2026-07-29", "2026-08-04")


def test_explicit_window_passes_through() -> None:
    assert resolve_window("2023-06-01", "2023-06-30", today=TODAY) == (
        "2023-06-01",
        "2023-06-30",
    )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"start_date": "2026-07-01", "end_date": "2026-07-05", "past_days": 3}, "not both"),
        ({"start_date": "2026-07-01"}, "end_date is missing"),
        ({"end_date": "2026-07-05"}, "start_date is missing"),
        ({"start_date": "01/07/2026", "end_date": "2026-07-05"}, "YYYY-MM-DD"),
        ({"start_date": "2026-07-05", "end_date": "2026-07-01"}, "is after"),
        ({"past_days": -1}, "must not be negative"),
        ({"forecast_days": -3}, "must not be negative"),
        ({"past_days": 1.5}, "whole number of days"),
        ({"past_days": 0}, "no days at all"),
        ({"forecast_days": 0}, "no days at all"),
    ],
)
def test_malformed_windows_are_rejected_before_any_fetch(kwargs: dict, message: str) -> None:
    with pytest.raises(InvalidWindowError, match=message):
        resolve_window(today=TODAY, **kwargs)


@pytest.mark.parametrize(
    ("kwargs", "window"),
    [
        ({"past_days": 400}, ("2025-06-24", "2026-07-28")),
        ({"forecast_days": 30}, ("2026-07-29", "2026-08-27")),
        ({"start_date": "1990-01-01", "end_date": "1990-01-02"}, ("1990-01-01", "1990-01-02")),
    ],
)
def test_a_far_reaching_window_resolves_rather_than_failing(
    kwargs: dict, window: tuple[str, str]
) -> None:
    """No cap lives here: reach is a source question, not an argument fault.

    The back window has no bound at all (the reanalysis serves decades) and the
    forward one is the wrapper's ``not_available`` horizon against ``ctx.as_of``,
    so neither may be rejected as malformed here (``decisions.md`` § Window
    resolution and the scenario clock, § Typed abstention).
    """
    assert resolve_window(today=TODAY, **kwargs) == window


# --- The forecast horizon: the tool's one typed `not_available` (T041) --------


class RecordingWeatherClient:
    """A ``WeatherClient`` that records every window it was asked for."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def fetch(self, *, start_date: str, end_date: str) -> WeatherResult:
        self.calls.append((start_date, end_date))
        return WeatherResult(
            latitude=51.353484,
            longitude=12.432152,
            timezone="Europe/Berlin",
            source="archive",
            data=[
                DailyWeatherRow(
                    Date=start_date,
                    tm=10.0,
                    tx=15.0,
                    tn=5.0,
                    rf=70.0,
                    precip=0.0,
                    w=5.0,
                    gs=1000.0,
                )
            ],
        )


def _tool() -> tuple[Any, RecordingWeatherClient]:
    """The weather tool bound to a context frozen at :data:`AS_OF`.

    The source goes in through ``ctx.weather``, which is the seam itself — no
    monkeypatching a module attribute, because a wrapper that reached past its
    context would still pass that.
    """
    client = RecordingWeatherClient()
    ctx = ScenarioContext.bound(clock=lambda: AS_OF, db=None, weather=client)
    return weather_module.make_weather_forecast_tool(ctx), client


def test_a_window_past_the_horizon_is_not_available_not_an_error() -> None:
    """The single scope limit: typed, and never reaching a source."""
    tool, client = _tool()
    day_after = AS_OF.date() + timedelta(days=FORECAST_HORIZON_DAYS + 1)

    result = asyncio.run(tool(start_date=day_after.isoformat(), end_date=day_after.isoformat()))

    assert result["status"] == "not_available"
    assert "16 days" in result["reason"]
    assert not client.calls, "the horizon must be checked before any source is asked"


def test_the_horizon_is_measured_from_as_of_not_the_wall_clock() -> None:
    """The last answerable day is exactly ``as_of + 16``; the next one is not.

    Both are in the real past, so a wall clock would serve either without
    complaint — the check is only meaningful against the case's own ``as_of``.
    """
    tool, client = _tool()
    last = AS_OF.date() + timedelta(days=FORECAST_HORIZON_DAYS)

    inside = asyncio.run(tool(start_date=last.isoformat(), end_date=last.isoformat()))
    outside = asyncio.run(
        tool(start_date=last.isoformat(), end_date=(last + timedelta(days=1)).isoformat())
    )

    assert inside["status"] == "success"
    assert outside["status"] == "not_available"
    assert [end for _, end in client.calls] == [last.isoformat()]


def test_the_horizon_binds_the_relative_window_form_too() -> None:
    """``forecast_days`` is checked on the resolved window, not on the count."""
    tool, client = _tool()

    assert asyncio.run(tool(forecast_days=FORECAST_HORIZON_DAYS + 1))["status"] == "success"
    assert asyncio.run(tool(forecast_days=FORECAST_HORIZON_DAYS + 2))["status"] == "not_available"
    assert len(client.calls) == 1


def test_a_long_back_window_is_never_the_horizon_s_business() -> None:
    """Only the forward side is bounded — the back window reaches as far as asked."""
    tool, client = _tool()

    result = asyncio.run(tool(past_days=400))

    assert result["status"] == "success"
    assert client.calls[0][0] == (AS_OF.date() - timedelta(days=400)).isoformat()
