"""The cap both daily series are bounded by, and the aggregates that replace one.

A tool's summary is sufficient on its own to answer; the series beside it is a
product too — plot specs, counterfactual comparisons and per-day answers all need
days — so it is capped rather than dropped, with the truncation flagged
(``decisions.md`` § Bounded series). An absolute Archive window is otherwise
unbounded: nothing limits how far back a window may reach, so without this a
single call could return a decade of rows.

The bound is on the **response**, never on the computation. GR2L still runs every
day of the window — the water balance carries state from one day to the next, so
a capped simulation would be a different simulation — and the summary and the
measured comparison are still derived from the full series. Only what the model
reads back is bounded.

Why fixed-size buckets from the window's first day rather than ISO weeks: an ISO
bucketing makes the first and last bucket's length depend on which weekday the
window happened to open, so two windows of equal length summarize differently.
Here every bucket holds :data:`WEEK_DAYS` days except possibly the last, which
reports its own ``days``.

Each field is aggregated the way that field is *defined*: fluxes accumulate and
states average, and for the weather row the rule is the station derivation's own
(``tx`` a max, ``tn`` a min, ``precip`` and ``gs`` sums, the rest means). A weekly
``tx`` is therefore the week's hottest day, not a mean of maxima.
"""

from water_assistant_agent.assistant.tools.schemas import (
    DailyWeatherRow,
    GreenRoofDay,
    RoofPeriod,
    WeatherPeriod,
)

MAX_SERIES_DAYS = 31
"""Longest daily series either tool returns. Beyond it, weekly aggregates.

One month of days: long enough that every per-day question the catalog asks can
be answered from the series itself, short enough that the answer's accuracy does
not depend on how much of a long window survived the turn
(``agent_architecture.md`` §3.3, ``decisions.md`` § Bounded series).
"""

WEEK_DAYS = 7
"""Bucket size for a truncated series."""


def _mean(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return round(sum(present) / len(present), 2) if present else None


def _total(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return round(sum(present), 2) if present else None


def _min(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return round(min(present), 2) if present else None


def _chunks(rows: list, size: int) -> list[list]:
    """*rows* in consecutive buckets of *size*, the last one short if it must be."""
    return [rows[start : start + size] for start in range(0, len(rows), size)]


def weather_periods(rows: list[DailyWeatherRow], size: int) -> list[WeatherPeriod]:
    """Aggregate *rows* into buckets of *size* days.

    Pass ``size=len(rows)`` for one whole-window summary and :data:`WEEK_DAYS`
    for the weekly series; both are the same aggregation, so the summary can
    never disagree with the buckets it summarizes.
    """
    periods: list[WeatherPeriod] = []
    for chunk in _chunks(rows, size):
        periods.append(
            WeatherPeriod(
                start=chunk[0].Date,
                end=chunk[-1].Date,
                days=len(chunk),
                tm=_mean([row.tm for row in chunk]),
                tx=round(max(row.tx for row in chunk), 2),
                tn=round(min(row.tn for row in chunk), 2),
                rf=_mean([row.rf for row in chunk]),
                precip=_total([row.precip for row in chunk]),
                w=_mean([row.w for row in chunk]),
                gs=_total([row.gs for row in chunk]),
            )
        )
    return periods


def roof_periods(days: list[GreenRoofDay], size: int) -> list[RoofPeriod]:
    """Aggregate GR2L *days* into buckets of *size* days.

    Fluxes (``ET_PM``, ``ET``, ``Qdown``, ``Qup``, ``OUT``) accumulate over the
    bucket; states (``Ssub``, ``Sret``, ``swc_pct``) are means, with the driest
    day kept alongside because a mean water content hides exactly the day a
    drought question is about. Day 1's null flux terms are skipped rather than
    read as zeros.
    """
    periods: list[RoofPeriod] = []
    for chunk in _chunks(days, size):
        swc = [day.swc_pct for day in chunk]
        periods.append(
            RoofPeriod(
                start=chunk[0].Date,
                end=chunk[-1].Date,
                days=len(chunk),
                ET_PM=_total([day.ET_PM for day in chunk]),
                ET=_total([day.ET for day in chunk]),
                Qdown=_total([day.Qdown for day in chunk]),
                Qup=_total([day.Qup for day in chunk]),
                OUT=_total([day.OUT for day in chunk]),
                Ssub=_mean([day.Ssub for day in chunk]),
                Sret=_mean([day.Sret for day in chunk]),
                swc_pct=_mean(swc),
                min_swc_pct=_min(swc),
            )
        )
    return periods
