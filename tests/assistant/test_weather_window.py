"""Relative → absolute weather-window resolution (``weather_client.resolve_window``).

The bug these pin: Open-Meteo defaults ``forecast_days`` to 7, so forwarding a bare
``past_days`` returned the requested history *plus* a week of forecast, unlabelled in
the transposed rows. Resolution happens in the tool wrappers now, so the client only
ever sends an explicit window.
"""

from __future__ import annotations

from datetime import date

import pytest

from water_assistant_agent.assistant.tools.weather_client import (
    InvalidWindowError,
    resolve_window,
)

TODAY = date(2026, 7, 29)


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
        ({"past_days": -1}, "between 0 and 92"),
        ({"past_days": 400}, "between 0 and 92"),
        ({"forecast_days": 30}, "between 0 and 16"),
        ({"past_days": 0}, "no days at all"),
        ({"forecast_days": 0}, "no days at all"),
    ],
)
def test_malformed_windows_are_rejected_before_any_fetch(kwargs: dict, message: str) -> None:
    with pytest.raises(InvalidWindowError, match=message):
        resolve_window(today=TODAY, **kwargs)
