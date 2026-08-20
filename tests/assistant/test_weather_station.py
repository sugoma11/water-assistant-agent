"""The station source and the routing that reaches it (T055, packet P2a).

Everything here runs against the real pinned ``data/water.duckdb``, because the
station *is* that file: a fixture-backed test would pin an aggregation over rows
this deployment never serves.

Two conventions, both carried from T035a-c and both load-bearing. Expected values
are **hand-computed in Python from the raw half-hourly rows** — ``statistics``
and ``sum`` over what the table holds — never by running a second copy of the
derivation's own SQL, so a test cannot pass by agreeing with the implementation
it is testing. And each routing decision is asserted from the *far* side: what
the Archive half was actually asked for, rather than what the composite looks
like from outside.
"""

from __future__ import annotations

import asyncio
import statistics
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import pytest

from water_assistant_agent.assistant.cache import CacheMissError, ResponseCache
from water_assistant_agent.assistant.context import ScenarioContext
from water_assistant_agent.assistant.tools import gr2l as gr2l_module
from water_assistant_agent.assistant.tools import weather as weather_module
from water_assistant_agent.assistant.tools import weather_client as weather_client_module
from water_assistant_agent.assistant.tools.schemas import (
    DailyWeatherRow,
    Gr2lResultRow,
    WeatherResult,
)
from water_assistant_agent.assistant.tools.weather_client import (
    CompositeWeatherClient,
    make_weather_client,
)
from water_assistant_agent.assistant.tools.weather_station import (
    ROWS_PER_COMPLETE_DAY,
    StationWeatherSource,
)

DB_PATH = "data/water.duckdb"
BERLIN = ZoneInfo("Europe/Berlin")
UTC = ZoneInfo("UTC")

# The record's complete-day edges, pinned in `eval/pins.json` by T044 and
# restated here so a test failure names the edge that moved.
FIRST_COMPLETE_DAY = date(2025, 1, 2)
LAST_COMPLETE_DAY = date(2026, 4, 26)

# Far enough past the record's end that the as-of view holds all of it — these
# tests are about the *record's* edges, not about a cut inside it.
AFTER_RECORD = datetime(2026, 5, 1, 0, 0, tzinfo=BERLIN)

# A cut inside the record, for the tests that are about the view rather than the
# record. 11:00 local, so its own Berlin day is deliberately half-present.
MID_RECORD = datetime(2026, 3, 15, 11, 0, tzinfo=BERLIN)


# --- Reading the raw record, independently of the derivation ------------------


def _raw_half_hours() -> dict[date, list[tuple[float, ...]]]:
    """Every half-hourly row, bucketed into Berlin days in Python.

    No ``GROUP BY``, no ``AT TIME ZONE`` — the conversion is done here with
    ``zoneinfo`` so the day boundary the derivation claims can be checked against
    an independent implementation of the same rule.
    """
    connection = duckdb.connect(DB_PATH, read_only=True)
    try:
        rows = connection.execute(
            'SELECT timestamp, "Tmean", "Tmax", "Rain", "Rad_SW", "RH", "windspeed" '
            "FROM wetter ORDER BY timestamp"
        ).fetchall()
    finally:
        connection.close()

    days: dict[date, list[tuple[float, ...]]] = {}
    for timestamp, *values in rows:
        berlin_day = timestamp.replace(tzinfo=UTC).astimezone(BERLIN).date()
        days.setdefault(berlin_day, []).append(tuple(values))
    return days


RAW_DAYS = _raw_half_hours()


def _hand_derive(rows: list[tuple[float, ...]]) -> dict[str, float]:
    """``weather_tool.md`` § Station source, written out in plain Python."""
    tmean, tmax, rain, rad, humidity, wind = (list(column) for column in zip(*rows, strict=True))
    return {
        "tm": statistics.fmean(tmean),
        "tx": max(tmax),
        "tn": min(2 * mean - high for mean, high in zip(tmean, tmax, strict=True)),
        "rf": statistics.fmean(humidity),
        "precip": sum(rain),
        "w": statistics.fmean([speed for speed in wind if speed >= 0]) * 3.6,
        "gs": sum(rad) * 1800.0 / 1e4,
    }


def _source(as_of: datetime = AFTER_RECORD) -> StationWeatherSource:
    """A station source over a context frozen at *as_of*."""
    return StationWeatherSource(_context(as_of).db)


def _context(as_of: datetime, **kwargs: Any) -> ScenarioContext:
    return ScenarioContext(
        clock=lambda: as_of,
        db_path=DB_PATH,
        weather_client_factory=kwargs.pop("factory", lambda db, cache: None),
        http_cache=kwargs.pop("cache", None),
    )


class SpyArchive:
    """The Open-Meteo half, replaced by something that records and never calls out."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def fetch(self, *, start_date: str, end_date: str) -> WeatherResult:
        self.calls.append((start_date, end_date))
        span = (date.fromisoformat(end_date) - date.fromisoformat(start_date)).days
        return WeatherResult(
            latitude=51.353484,
            longitude=12.432152,
            timezone="Europe/Berlin",
            source="archive",
            data=[
                DailyWeatherRow(
                    Date=(date.fromisoformat(start_date) + timedelta(days=offset)).isoformat(),
                    tm=10.0,
                    tx=15.0,
                    tn=5.0,
                    rf=70.0,
                    precip=0.0,
                    w=5.0,
                    gs=1000.0,
                )
                for offset in range(span + 1)
            ],
        )


def _composite(as_of: datetime) -> tuple[CompositeWeatherClient, SpyArchive]:
    """The real station half over the pinned DB, against a recording Archive half."""
    archive = SpyArchive()
    return CompositeWeatherClient(StationWeatherSource(_context(as_of).db), archive), archive


def _fetch(client: Any, start: date, end: date) -> WeatherResult:
    return asyncio.run(client.fetch(start_date=start.isoformat(), end_date=end.isoformat()))


# --- The derivation, field by field ------------------------------------------


@pytest.mark.parametrize(
    "day",
    [
        # A quiet mid-record winter day: nothing unusual, so any field that is
        # simply wrong shows up here rather than behind a special case.
        date(2026, 3, 15),
        # 18 of 48 wind samples are sentinel, and its rain is the far side of a
        # midnight straddle — two of the rules at once.
        date(2025, 4, 21),
        # The worst sentinel day in the record, and a wet one.
        date(2025, 8, 28),
        # High summer: `gs` an order of magnitude above the winter days, so a
        # radiation conversion off by 10³ cannot hide in a plausible number.
        date(2025, 6, 15),
    ],
)
def test_every_field_matches_a_hand_computed_value(day: date) -> None:
    """The seven fields, against arithmetic done outside the query that produced them."""
    (row,) = _source().daily_rows(day, day)
    expected = _hand_derive(RAW_DAYS[day])

    assert row.Date == day.isoformat()
    for field, value in expected.items():
        # Tight: the two differ only by float summation order, never by rule.
        assert getattr(row, field) == pytest.approx(value, rel=1e-12), field


def test_tn_is_the_estimator_and_not_the_minimum_mean() -> None:
    """``min(2·Tmean − Tmax)``, which is a different number from ``min(Tmean)``.

    The record has no ``Tmin`` column; the estimate is the whole reason the field
    exists at all, and the naive alternative it is distinguished from would pass
    a test that only checked ``tn <= tm``.
    """
    day = date(2026, 3, 15)
    (row,) = _source().daily_rows(day, day)
    naive = min(mean for mean, *_ in RAW_DAYS[day])

    assert row.tn == pytest.approx(
        min(2 * mean - high for mean, high, *_ in RAW_DAYS[day]), rel=1e-12
    )
    assert row.tn != pytest.approx(naive, rel=1e-6)


def test_the_two_unit_conversions_are_applied() -> None:
    """``w`` in km/h and ``gs`` in J/cm²/day — the two GR2L gets wrong if left raw."""
    day = date(2026, 3, 15)
    (row,) = _source().daily_rows(day, day)
    raw_wind = statistics.fmean([speed for *_, speed in RAW_DAYS[day] if speed >= 0])
    raw_radiation = sum(rad for _, _, _, rad, _, _ in RAW_DAYS[day])

    assert row.w == pytest.approx(raw_wind * 3.6, rel=1e-12)
    assert row.gs == pytest.approx(raw_radiation * 1800.0 / 1e4, rel=1e-12)


def test_rows_are_served_uncorrected() -> None:
    """No calibration factor is applied to `gs`, though the offset is measured.

    The station reads ~26 % low on shortwave against the site's own pyranometers
    and the overlap makes the factor computable — which is exactly why
    ``decisions.md`` § No fitted correction between the instrument and the oracle
    forbids applying it. This test is the standing guard on that.
    """
    day = date(2025, 6, 15)
    (row,) = _source().daily_rows(day, day)

    assert row.gs == pytest.approx(_hand_derive(RAW_DAYS[day])["gs"], rel=1e-12)


# --- The day boundary ---------------------------------------------------------


def test_rain_straddling_local_midnight_lands_in_the_right_berlin_day() -> None:
    """2025-04-20's last four UTC half-hours are 2025-04-21 in Berlin, and carry 6.664 mm.

    April is CEST, so 22:00 UTC is 00:00 local. Grouped in UTC the rain reads
    12.087 mm on the 20th and 0.119 mm on the 21st; in Berlin days it reads
    5.440 and 6.783. The lysimeters under that gauge recorded the runoff on the
    21st, so grouping in UTC would desynchronise the forcing from the very
    measurements the model is validated against.
    """
    connection = duckdb.connect(DB_PATH, read_only=True)
    try:
        late = connection.execute(
            "SELECT sum(\"Rain\") FROM wetter "
            "WHERE timestamp >= TIMESTAMP '2025-04-20 22:00:00' "
            "AND timestamp < TIMESTAMP '2025-04-21 00:00:00'"
        ).fetchone()[0]
        utc_20, utc_21 = (
            connection.execute(
                f"SELECT sum(\"Rain\") FROM wetter WHERE timestamp::DATE = DATE '{day}'"
            ).fetchone()[0]
            for day in ("2025-04-20", "2025-04-21")
        )
    finally:
        connection.close()

    rows = _source().daily_rows(date(2025, 4, 20), date(2025, 4, 21))
    berlin = {row.Date: row.precip for row in rows}

    assert late == pytest.approx(6.664, abs=1e-9)
    # The migrated total is the whole difference between the two groupings.
    assert berlin["2025-04-21"] == pytest.approx(utc_21 + late, rel=1e-12)
    assert berlin["2025-04-20"] == pytest.approx(utc_20 - late + 0.017, rel=1e-3)
    # ...and it is a difference big enough to move a peak-day argmax: under UTC
    # grouping the 20th is the wetter day, under Berlin grouping the 21st is.
    assert utc_20 > utc_21
    assert berlin["2025-04-21"] > berlin["2025-04-20"]


# --- The sentinel filter ------------------------------------------------------


def test_the_wind_sentinel_is_excluded_from_the_mean() -> None:
    """18 of 48 samples on 2025-08-28 are sentinel; including them inverts the sign."""
    day = date(2025, 8, 28)
    (row,) = _source().daily_rows(day, day)
    winds = [speed for *_, speed in RAW_DAYS[day]]

    assert sum(1 for speed in winds if speed < 0) == 18
    assert row.w == pytest.approx(
        statistics.fmean([speed for speed in winds if speed >= 0]) * 3.6, rel=1e-12
    )
    # Unfiltered, this day's mean wind would be about −10,418 km/h.
    assert statistics.fmean(winds) * 3.6 < -1000
    assert row.w > 0


def test_the_filter_is_by_sign_because_equality_would_not_catch_them() -> None:
    """2025-09-25 carries six contaminated samples and **not one** equal to −7999.

    The half-hourly values are themselves means of finer samples, so a half-hour
    mixing sentinel and real readings lands anywhere in between. Filtering on
    ``== -7999`` — the value ``findings.md`` names — would leave every one of
    this day's six in, and its mean wind would read about −799 km/h.
    """
    day = date(2025, 9, 25)
    (row,) = _source().daily_rows(day, day)
    winds = [speed for *_, speed in RAW_DAYS[day]]

    assert not any(speed == -7999.0 for speed in winds)
    assert sum(1 for speed in winds if speed < 0) == 6
    equality_filtered = statistics.fmean([speed for speed in winds if speed != -7999.0]) * 3.6
    assert equality_filtered < -700
    assert row.w == pytest.approx(
        statistics.fmean([speed for speed in winds if speed >= 0]) * 3.6, rel=1e-12
    )


# --- Completeness -------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "rows_held"),
    [
        (date(2025, 1, 1), 46),  # the record opens at 01:00 local
        (date(2025, 3, 30), 44),  # spring forward: a 23-hour day
        (date(2026, 3, 29), 44),  # the same, inside the catalog's `as_of` band
        (date(2025, 10, 26), 50),  # fall back: a 25-hour day
        (date(2026, 4, 27), 23),  # the record's last, partial day
    ],
)
def test_an_incomplete_day_is_not_served(day: date, rows_held: int) -> None:
    """A day without exactly 48 half-hours is absent, never partially derived."""
    assert len(RAW_DAYS[day]) == rows_held != ROWS_PER_COMPLETE_DAY
    assert _source().daily_rows(day, day) == []


def test_the_record_bounds_are_the_first_and_last_complete_day() -> None:
    """Not the record's first and last *timestamp* — both edge days are partial."""
    assert _source().record_bounds() == (FIRST_COMPLETE_DAY, LAST_COMPLETE_DAY)


def test_the_bounds_are_read_through_the_as_of_view() -> None:
    """A case's last complete day is its cut's, not the pinned file's."""
    first, last = _source(MID_RECORD).record_bounds()

    assert first == FIRST_COMPLETE_DAY
    # 11:00 local on the 15th leaves that day half-present, so the 14th is last.
    assert last == MID_RECORD.date() - timedelta(days=1)


# --- Routing ------------------------------------------------------------------


def test_a_window_the_record_covers_whole_is_served_by_the_station() -> None:
    client, archive = _composite(AFTER_RECORD)

    result = _fetch(client, date(2025, 6, 10), date(2025, 6, 16))

    assert result.source == "station"
    assert [row.Date for row in result.data] == [
        f"2025-06-{day}" for day in range(10, 17)
    ]
    assert archive.calls == [], "the station window must not reach Open-Meteo at all"


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (FIRST_COMPLETE_DAY, FIRST_COMPLETE_DAY + timedelta(days=2)),
        (LAST_COMPLETE_DAY - timedelta(days=2), LAST_COMPLETE_DAY),
    ],
)
def test_both_record_edges_still_route_to_the_station(start: date, end: date) -> None:
    """The edges are inclusive: the first and last complete day are served."""
    client, archive = _composite(AFTER_RECORD)

    assert _fetch(client, start, end).source == "station"
    assert archive.calls == []


@pytest.mark.parametrize(
    ("start", "end", "why"),
    [
        (FIRST_COMPLETE_DAY - timedelta(days=1), FIRST_COMPLETE_DAY, "one day before the record"),
        (LAST_COMPLETE_DAY, LAST_COMPLETE_DAY + timedelta(days=1), "one day past the record"),
        (date(2026, 3, 28), date(2026, 3, 30), "a spring-forward Sunday inside the record"),
    ],
)
def test_a_partly_covered_window_falls_to_archive_whole(
    start: date, end: date, why: str
) -> None:
    """One missing day sends the **entire** window to Open-Meteo, not just that day.

    Splitting it would give one window two provenances, with a measured
    temperature offset between them (``decisions.md`` § Weather sources).
    """
    client, archive = _composite(AFTER_RECORD)

    result = _fetch(client, start, end)

    assert result.source == "archive", why
    assert archive.calls == [(start.isoformat(), end.isoformat())]
    assert [row.Date for row in result.data][0] == start.isoformat()


def test_a_future_window_never_resolves_to_the_station() -> None:
    """Past ``as_of`` the view holds nothing, so there is no future special case."""
    client, archive = _composite(MID_RECORD)
    start = MID_RECORD.date() + timedelta(days=1)
    end = start + timedelta(days=3)

    assert _fetch(client, start, end).source == "archive"
    assert archive.calls == [(start.isoformat(), end.isoformat())]


def test_coverage_is_tested_through_the_as_of_view_not_the_record() -> None:
    """The mechanism behind the previous test, at the cut itself.

    Both windows lie inside the *record*; only the earlier one lies inside the
    *view*. A coverage test written against the record's true end would serve
    both from the station and leak two hours of post-``as_of`` observation into
    the second.
    """
    client, archive = _composite(MID_RECORD)
    cut = MID_RECORD.date()

    inside = _fetch(client, cut - timedelta(days=2), cut - timedelta(days=1))
    spanning = _fetch(client, cut - timedelta(days=1), cut)

    assert inside.source == "station"
    assert spanning.source == "archive"
    assert archive.calls == [((cut - timedelta(days=1)).isoformat(), cut.isoformat())]


# --- End to end, through both wrappers ----------------------------------------


def test_the_weather_tool_reports_the_station_as_its_source() -> None:
    ctx = _context(AFTER_RECORD, factory=make_weather_client)
    tool = weather_module.make_weather_forecast_tool(ctx)

    result = asyncio.run(tool(start_date="2025-06-10", end_date="2025-06-16"))

    assert result["status"] == "success"
    assert result["source"] == "station"
    assert len(result["data"]) == 7


def test_the_station_source_is_echoed_end_to_end_through_gr2l(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retrospective model run is forced by the station, and says so.

    The rows GR2L receives are asserted to be the station's own, so this is the
    forcing rather than a label: an answer that discloses the station has to be
    describing the data the model actually ran on.
    """
    forcing: list[list[DailyWeatherRow]] = []

    async def fake_run_gr2l(rows, parameters, **kwargs):
        # `**kwargs` absorbs the wrapper's `cache=ctx.cache` (T054).
        forcing.append(rows)
        return [
            Gr2lResultRow(Date=row.Date, ET_PM=1.0, Ssub=10.0, ET=1.0) for row in rows
        ]

    monkeypatch.setattr(gr2l_module, "run_gr2l", fake_run_gr2l)

    ctx = _context(AFTER_RECORD, factory=make_weather_client)
    tool = gr2l_module.make_green_roof_balance_tool(ctx)

    result = asyncio.run(
        tool("non_irrigated_extensive", start_date="2025-06-10", end_date="2025-06-16")
    )

    assert result["status"] == "success"
    assert result["weather_source"] == "station"

    station_rows = StationWeatherSource(ctx.db).daily_rows(date(2025, 6, 10), date(2025, 6, 16))
    assert forcing[0] == station_rows


# --- Replay: zero cache entries, zero live calls ------------------------------


def test_a_retrospective_station_case_replays_with_no_cache_and_no_live_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The packet's exit criterion, asserted on all three of its clauses.

    Replay is the strictest mode — ``allow_live=False`` makes any cache miss a
    hard :class:`CacheMissError` — and the station path is nonetheless answered
    in full, because the coverage test runs before anything holding a cache is
    reached. The cache directory is still empty afterwards: a station window is a
    pure function of a database the ``water_duckdb_sha256`` pin already covers,
    so there is nothing for an entry to add.
    """

    async def no_live_call(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the station path must not reach Open-Meteo")

    monkeypatch.setattr(weather_client_module, "fetch_daily_weather", no_live_call)

    cache_dir = tmp_path / "cache"
    ctx = _context(
        AFTER_RECORD,
        factory=lambda db, cache: make_weather_client(db, cache, allow_live=False),
        cache=ResponseCache(cache_dir),
    )
    tool = weather_module.make_weather_forecast_tool(ctx)

    result = asyncio.run(tool(start_date="2025-06-10", end_date="2025-06-16"))

    assert result["status"] == "success"
    assert result["source"] == "station"
    assert len(result["data"]) == 7
    assert list(cache_dir.iterdir()) == [], "a station window recorded a cache entry"


def test_the_same_window_uncovered_is_a_hard_replay_miss(tmp_path: Path) -> None:
    """The other half of the claim: replay is genuinely strict, so the pass above means something.

    Move the window one day past the record and the identical setup fails loudly
    rather than calling out — which is what makes "zero live calls" on the
    covered window evidence about routing rather than about a disabled client.
    """
    cache_dir = tmp_path / "cache"
    client = make_weather_client(
        _context(AFTER_RECORD).db, ResponseCache(cache_dir), allow_live=False
    )

    with pytest.raises(CacheMissError):
        _fetch(client, LAST_COMPLETE_DAY, LAST_COMPLETE_DAY + timedelta(days=1))

    assert list(cache_dir.iterdir()) == []
