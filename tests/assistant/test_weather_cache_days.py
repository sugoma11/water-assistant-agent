"""The weather cache is keyed by day, and a window is assembled from days (T146).

The property under test is not "the cache works" — ``test_cache.py`` owns that —
but the one the measurement run needed and did not have: **a window nobody ever
captured as a window still replays, provided its days were captured.** A rollout
reaches a window through ``past_days`` / ``forecast_days`` as readily as through
dates, so the windows a candidate can resolve are a plane over two axes while the
days they are drawn from are a list; keying on the window meant a capture pass
could only ever warm a line through that plane, and the holdout lost most of its
rollouts to the rest of it.

Every test here stubs :func:`fetch_daily_weather` at the module seam the rest of
the suite uses, so what is asserted is *which windows the client asked the
network for* — never how many files landed where. The economy claim is part of
the design: a cold 31-day window has to cost one request, not 31, or the fix
would trade a replay miss for a rate limit.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from water_assistant_agent.assistant.cache import CacheMissError, ResponseCache
from water_assistant_agent.assistant.tools import weather_client as weather_client_module
from water_assistant_agent.assistant.tools.schemas import DailyWeatherRow, WeatherResult
from water_assistant_agent.assistant.tools.weather_client import (
    ArchiveWeatherClient,
    IncompleteWeatherError,
    days_in_window,
)

# The grid cell Archive answers this site from — deliberately not the site's own
# coordinates, so a test cannot pass by confusing the request with the response.
CELL = (51.35325, 12.509026)


class SpyArchive:
    """A stand-in for :func:`fetch_daily_weather` that records the windows it is asked for.

    The rows are a deterministic function of the day, so a day assembled into one
    window is comparable with the same day assembled into another.
    """

    def __init__(self, *, serves: int | None = None) -> None:
        self.windows: list[tuple[str, str]] = []
        self._serves = serves
        """How many days of each window to answer with — ``None`` for all of them.

        A backend that answers short is the one failure the assembly cannot paper
        over, so it has to be producible here.
        """

    async def __call__(
        self,
        latitude: float,
        longitude: float,
        *,
        start_date: str,
        end_date: str,
        client: Any = None,
    ) -> WeatherResult:
        self.windows.append((start_date, end_date))
        days = days_in_window(start_date, end_date)
        if self._serves is not None:
            days = days[: self._serves]
        return WeatherResult(
            latitude=CELL[0],
            longitude=CELL[1],
            timezone="Europe/Berlin",
            source="archive",
            data=[_row(day) for day in days],
        )


def _row(day: str) -> DailyWeatherRow:
    """One day's weather, as a function of the day and of nothing else."""
    offset = (date.fromisoformat(day) - date(2026, 3, 1)).days
    return DailyWeatherRow(
        Date=day,
        tm=10.0 + offset,
        tx=15.0 + offset,
        tn=5.0 + offset,
        rf=70.0,
        precip=float(offset),
        w=5.0,
        gs=1000.0,
    )


@pytest.fixture
def archive(monkeypatch: pytest.MonkeyPatch) -> SpyArchive:
    spy = SpyArchive()
    monkeypatch.setattr(weather_client_module, "fetch_daily_weather", spy)
    return spy


def fetch(client: ArchiveWeatherClient, start: str, end: str) -> WeatherResult:
    return asyncio.run(client.fetch(start_date=start, end_date=end))


def entries(cache_dir: Path) -> int:
    return sum(1 for _ in cache_dir.glob("*.json"))


# ── The unit that is committed ────────────────────────────────────────────────


def test_a_window_is_committed_one_day_at_a_time(
    tmp_path: Path, archive: SpyArchive
) -> None:
    cache = ResponseCache(tmp_path)
    client = ArchiveWeatherClient(cache)

    fetch(client, "2026-03-01", "2026-03-05")

    assert archive.windows == [("2026-03-01", "2026-03-05")], "one request, five entries"
    assert entries(tmp_path) == 5
    for day in days_in_window("2026-03-01", "2026-03-05"):
        committed = cache.get(client.day_request(day))
        assert committed is not None, f"{day} is not committed under its own key"
        assert [row["Date"] for row in committed["data"]] == [day]


def test_the_assembled_window_is_what_one_fetch_would_have_returned(
    tmp_path: Path, archive: SpyArchive
) -> None:
    """Assembly is a re-keying, not a transformation: provenance and rows survive it."""
    direct = fetch(ArchiveWeatherClient(None), "2026-03-01", "2026-03-05")
    assembled = fetch(ArchiveWeatherClient(ResponseCache(tmp_path)), "2026-03-01", "2026-03-05")

    assert assembled == direct


# ── The property the measurement run needed ───────────────────────────────────


def test_a_window_never_captured_as_a_window_still_replays(
    tmp_path: Path, archive: SpyArchive
) -> None:
    """T146 in one assertion: the days were captured, the window never was.

    A candidate reading "the next three days" one day differently from the oracle
    used to miss outright and lose its case. Here the capture pass warmed
    03-01..03-05 and the rollout asks for 03-02..03-04 — a window with no entry
    of its own — under a client that may not call out at all.
    """
    fetch(ArchiveWeatherClient(ResponseCache(tmp_path)), "2026-03-01", "2026-03-05")
    archive.windows.clear()

    replayed = fetch(
        ArchiveWeatherClient(ResponseCache(tmp_path), allow_live=False),
        "2026-03-02",
        "2026-03-04",
    )

    assert archive.windows == [], "replay reached the network"
    assert [row.Date for row in replayed.data] == ["2026-03-02", "2026-03-03", "2026-03-04"]
    assert replayed.source == "archive"


def test_only_the_gaps_are_fetched_and_each_run_costs_one_request(
    tmp_path: Path, archive: SpyArchive
) -> None:
    """The economy claim. Held days are never re-requested; a gap is one request.

    Two gaps rather than one, because the interesting case is not "the window
    extends past what is held" but "the window straddles what is held" — which is
    what a candidate's window does against a capture pass's.
    """
    cache = ResponseCache(tmp_path)
    client = ArchiveWeatherClient(cache)
    fetch(client, "2026-03-03", "2026-03-04")
    fetch(client, "2026-03-08", "2026-03-08")
    archive.windows.clear()

    result = fetch(client, "2026-03-01", "2026-03-10")

    assert archive.windows == [
        ("2026-03-01", "2026-03-02"),
        ("2026-03-05", "2026-03-07"),
        ("2026-03-09", "2026-03-10"),
    ]
    assert [row.Date for row in result.data] == days_in_window("2026-03-01", "2026-03-10")


def test_a_cold_month_is_one_request_and_thirty_one_entries(
    tmp_path: Path, archive: SpyArchive
) -> None:
    """The cap's worth of days, bought at the price the window key used to cost."""
    fetch(ArchiveWeatherClient(ResponseCache(tmp_path)), "2026-03-01", "2026-03-31")

    assert archive.windows == [("2026-03-01", "2026-03-31")]
    assert entries(tmp_path) == 31


# ── What a day it does not hold still costs ───────────────────────────────────


def test_replay_refuses_a_day_it_does_not_hold(
    tmp_path: Path, archive: SpyArchive
) -> None:
    """Replay is still replay: the miss is hard, and it names the day."""
    fetch(ArchiveWeatherClient(ResponseCache(tmp_path)), "2026-03-01", "2026-03-02")
    archive.windows.clear()
    client = ArchiveWeatherClient(ResponseCache(tmp_path), allow_live=False)

    with pytest.raises(CacheMissError) as raised:
        fetch(client, "2026-03-01", "2026-03-03")

    assert archive.windows == []
    assert raised.value.canonical_request == client.day_request("2026-03-03")


def test_a_backend_that_answers_short_is_an_error_not_a_hole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A window assembled with a day missing would be silently shifted downstream.

    Every consumer reads these rows positionally against the window it asked for
    — GR2L's forcing, the irrigation seed, a plot's x axis — so a short answer has
    to fail here rather than three layers down as an off-by-one in a result.
    """
    monkeypatch.setattr(
        weather_client_module, "fetch_daily_weather", SpyArchive(serves=1)
    )
    client = ArchiveWeatherClient(ResponseCache(tmp_path))

    with pytest.raises(CacheMissError, match="no row for"):
        fetch(client, "2026-03-01", "2026-03-03")

    assert entries(tmp_path) == 0, "a fill that could not be completed commits nothing"


def test_an_entry_holding_the_wrong_day_is_refused(
    tmp_path: Path, archive: SpyArchive
) -> None:
    """The assembly checks the days it got against the days it asked for.

    Written by hand into the cache because nothing in the client can produce it —
    which is the point: the check is there for a committed entry that has been
    edited, re-keyed by hand, or migrated by a script with an off-by-one.
    """
    cache = ResponseCache(tmp_path)
    client = ArchiveWeatherClient(cache, allow_live=False)
    for day in days_in_window("2026-03-01", "2026-03-02"):
        cache.put(
            client.day_request(day),
            WeatherResult(
                latitude=CELL[0],
                longitude=CELL[1],
                timezone="Europe/Berlin",
                source="archive",
                data=[_row("2026-03-01")],
            ).model_dump(),
        )

    with pytest.raises(IncompleteWeatherError, match="do not reconstruct"):
        fetch(client, "2026-03-01", "2026-03-02")


# ── Production, which has no case and therefore no day to file ────────────────


def test_without_a_cache_the_whole_window_is_fetched_and_nothing_is_committed(
    tmp_path: Path, archive: SpyArchive
) -> None:
    """Production's binding: one request per call, no entry, no day split.

    A committed entry keyed on an absolute day would keep serving the *forecast*
    a day once returned after that day had become an observation, which is the
    one thing the running service must not do.
    """
    result = fetch(ArchiveWeatherClient(None), "2026-03-01", "2026-03-05")

    assert archive.windows == [("2026-03-01", "2026-03-05")]
    assert entries(tmp_path) == 0
    assert len(result.data) == 5


def test_replay_without_a_cache_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="needs a cache"):
        ArchiveWeatherClient(None, allow_live=False)


# ── The decomposition itself ──────────────────────────────────────────────────


def test_days_in_window_is_inclusive_at_both_ends() -> None:
    assert days_in_window("2026-03-01", "2026-03-01") == ["2026-03-01"]
    assert len(days_in_window("2026-03-01", "2026-03-31")) == 31


def test_days_in_window_crosses_a_month_and_a_year() -> None:
    assert days_in_window("2025-12-30", "2026-01-02") == [
        "2025-12-30",
        "2025-12-31",
        "2026-01-01",
        "2026-01-02",
    ]


def test_the_band_a_case_can_reach_is_a_list_and_not_a_plane() -> None:
    """Why the key moved, stated as arithmetic rather than as prose.

    ``resolve_window`` takes ``past_days`` and ``forecast_days`` independently, so
    the windows one ``as_of`` can produce are a product of two ranges while the
    days those windows are made of are their union — which is what makes a
    capture pass able to warm all of them.
    """
    as_of = date(2026, 3, 15)
    horizon = weather_client_module.FORECAST_HORIZON_DAYS

    windows = {
        (as_of - timedelta(days=past), as_of + timedelta(days=forward - 1))
        for past in range(horizon + 1)
        for forward in range(1, horizon + 2)
    }
    days = {day for start, end in windows for day in days_in_window(start.isoformat(), end.isoformat())}

    assert len(windows) == 289
    assert len(days) == 2 * horizon + 1 == 33
