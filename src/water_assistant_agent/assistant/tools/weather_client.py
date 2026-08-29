"""Daily weather: the two sources, the window rules, and the client that picks between them.

No ADK imports — this is the reusable seam behind both ``get_weather_forecast_tool``
and ``predict_green_roof_water_balance_tool``. It fetches the seven daily variables
GR2L needs, transposes Open-Meteo's column-oriented response into row-oriented
:class:`DailyWeatherRow` objects, and applies the two unit conversions GR2L's math
requires (``gs = shortwave_radiation_sum × 100`` to J/cm²/day; wind requested in
km/h). See ``weather_tool.md`` for the conversion rationale.

There are exactly **two sources**, chosen in code from the window and never named
by the agent: the site's own station (:mod:`weather_station`, whenever the record
covers the whole window) and Open-Meteo's ERA5 Archive for everything else.
:class:`CompositeWeatherClient` is where that choice is made and
:func:`make_weather_client` is what a :class:`~..context.ScenarioContext` calls to
build one.

:func:`fetch_daily_weather` takes an **absolute** window only. Relative windows are
resolved by :func:`resolve_window` in the tool wrappers, before the client is ever
called, because Open-Meteo's own ``past_days`` silently carries a seven-day forecast
tail: ``past_days=5`` returns five past days *plus* today and six forecast days, and
the rows are indistinguishable once transposed.

**A window is what the client is asked for; a day is what it caches** (T146).
:class:`ArchiveWeatherClient` decomposes every window into calendar days, looks
them up together and assembles the answer back, so two windows sharing a day
share its entry and no window has to have been captured as a window.
"""

import asyncio
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any, Protocol, runtime_checkable

import httpx
import structlog

from water_assistant_agent.assistant.cache import ResponseCache
from water_assistant_agent.assistant.ports import ReadOnlyWarehouseQuery
from water_assistant_agent.assistant.tools.schemas import DailyWeatherRow, WeatherResult
from water_assistant_agent.assistant.tools.site import (
    SITE_LATITUDE,
    SITE_LONGITUDE,
    SITE_TIMEZONE,
)
from water_assistant_agent.assistant.tools.weather_station import (
    STATION_SOURCE,
    StationWeatherSource,
)

logger = structlog.get_logger(__name__)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

ARCHIVE_SOURCE = "archive"
"""The provenance the Open-Meteo half stamps. The only Open-Meteo backend in use.

Open-Meteo's live Forecast endpoint is **retired here, not merely unused**: it
refuses a window as old as a case's ``as_of`` outright, it disagrees materially
with Archive on the same past day (2.50 mm vs 0.00 mm of rain at this site), and
with the station serving every window the record covers there is nothing left
for it to answer that Archive cannot (``findings.md`` § Weather source
measurements, ``decisions.md`` § Weather sources).
"""

# Open-Meteo's default when a request names no window at all; kept so a bare call
# still means "the coming week" after resolution.
_DEFAULT_FORECAST_DAYS = 7

FORECAST_HORIZON_DAYS = 16
"""How far past ``as_of`` a weather window may reach — the tool's one scope limit.

Load-bearing in code rather than upstream: Open-Meteo's Archive backend answers
day 400 past a case's ``as_of`` without complaint (it is still the real past),
so nothing but this check stops a future-facing window from being served as
though it were observed (``agent_architecture.md`` §3.3, ``decisions.md``
§ Typed abstention). It bounds only the **forward** side; nothing bounds how far
back a window may reach.
"""

# The seven daily variables GR2L consumes, in Open-Meteo naming.
_DAILY_VARS = ",".join(
    (
        "temperature_2m_mean",
        "temperature_2m_max",
        "temperature_2m_min",
        "relative_humidity_2m_mean",
        "precipitation_sum",
        "wind_speed_10m_mean",
        "shortwave_radiation_sum",
    )
)

# 1 MJ/m² = 100 J/cm²; GR2L expects gs in J/cm²/day (see weather_tool.md).
_RADIATION_MJ_TO_JCM2 = 100.0

_TIMEOUT_SECONDS = 30.0


class InvalidWindowError(ValueError):
    """The caller's date window is malformed — an argument fault, not a fetch failure.

    Raised before any HTTP hop, so the message names what to fix and the agent can
    retry with corrected arguments instead of being told the fetch failed.
    """


class WeatherFetchError(RuntimeError):
    """Upstream weather failure whose message is safe to hand back to the agent."""


class OpenMeteoError(WeatherFetchError):
    """Open-Meteo rejected the request; ``reason`` is its own explanation."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"Open-Meteo rejected the request: {reason}")
        self.reason = reason


class IncompleteWeatherError(WeatherFetchError):
    """The backend answered, but some day in the window has no data for a variable."""


class _ClientHolder:
    """Module-level singleton holder for the shared httpx client."""

    instance: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Lazily create and cache a module-level ``httpx.AsyncClient``."""
    if _ClientHolder.instance is None:
        _ClientHolder.instance = httpx.AsyncClient(timeout=_TIMEOUT_SECONDS)
    return _ClientHolder.instance


def _parse_date(value: str, field: str) -> date:
    """Parse one ``YYYY-MM-DD`` argument, naming the field it came from on failure."""
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise InvalidWindowError(
            f"{field} must be a YYYY-MM-DD date, got {value!r}."
        ) from None


def _validate_count(value: int, field: str) -> int:
    """Reject a relative-window count that is not a whole number of days ≥ 0.

    Only *malformedness* is checked here. There is deliberately no upper bound:
    no source imposes one on the back window — the station is bounded by the
    record it covers, the reanalysis reaches back decades — and the forward
    horizon is a scope limit the wrapper reports as ``not_available`` against
    ``ctx.as_of``, never an argument fault (``decisions.md`` § Window resolution
    and the scenario clock, § Typed abstention).
    """
    if not isinstance(value, int) or isinstance(value, bool):
        raise InvalidWindowError(f"{field} must be a whole number of days, got {value!r}.")
    if value < 0:
        raise InvalidWindowError(f"{field} must not be negative, got {value}.")
    return value


def resolve_window(
    start_date: str | None = None,
    end_date: str | None = None,
    past_days: int | None = None,
    forecast_days: int | None = None,
    *,
    today: date,
) -> tuple[str, str]:
    """Resolve either window form to one absolute ``(start, end)`` pair of ISO dates.

    Both tool wrappers call this before touching the client, so the client only ever
    sees an explicit window. That is what keeps a past-only request past-only:
    Open-Meteo defaults ``forecast_days`` to 7, so forwarding a bare ``past_days``
    appends a week of *predictions* to what the user asked to be history, with
    nothing in the returned rows to mark which is which.

    *today* is required and carries no wall-clock fallback — a caller who forgets
    it fails here rather than silently resolving a relative window against the
    host's real date, which would leak real time into a pinned case
    (`decisions.md` § Window resolution and the scenario clock).

    The relative form is read the way Open-Meteo splits it — ``past_days`` counts
    complete days ending yesterday, ``forecast_days`` counts from today forward:

    ============================  ==========================================
    Arguments                     Window
    ============================  ==========================================
    ``past_days=P``               ``today - P`` … ``today - 1`` (P days)
    ``forecast_days=F``           ``today`` … ``today + F - 1`` (F days)
    both                          ``today - P`` … ``today + F - 1``
    neither                       ``today`` … ``today + 6`` (the old default)
    ============================  ==========================================

    Window validity has exactly two failure modes, and only the first is raised
    here: a **malformed** window — mixed or half-given forms, an unparseable
    date, a negative count, a range that ends before it starts — is an argument
    fault. How far the resolved window reaches is not this function's business;
    the forward horizon is checked against ``ctx.as_of`` by the wrapper and
    reported as ``not_available`` (``agent_architecture.md`` §3.3).

    Raises:
        InvalidWindowError: mixed window forms, a half-given explicit window, an
            unparseable date, a negative count, a reversed range, or a pair of
            counts that selects no days at all.
    """
    has_absolute = start_date is not None or end_date is not None
    has_relative = past_days is not None or forecast_days is not None

    if has_absolute and has_relative:
        raise InvalidWindowError(
            "Give either an explicit start_date/end_date window or a relative "
            "past_days/forecast_days one, not both."
        )

    if has_absolute:
        if start_date is None or end_date is None:
            missing = "start_date" if start_date is None else "end_date"
            raise InvalidWindowError(f"An explicit window needs both dates; {missing} is missing.")
        start = _parse_date(start_date, "start_date")
        end = _parse_date(end_date, "end_date")
    else:
        past = 0 if past_days is None else _validate_count(past_days, "past_days")
        if past_days is None and forecast_days is None:
            future = _DEFAULT_FORECAST_DAYS
        else:
            future = (
                0 if forecast_days is None else _validate_count(forecast_days, "forecast_days")
            )
        start = today - timedelta(days=past)
        end = today + timedelta(days=future - 1)
        if start > end:
            raise InvalidWindowError(
                "past_days and forecast_days select no days at all; ask for at least one day."
            )

    if start > end:
        raise InvalidWindowError(f"start_date {start} is after end_date {end}.")
    return start.isoformat(), end.isoformat()


def beyond_horizon(end_date: str, as_of: date) -> bool:
    """True when *end_date* reaches further than :data:`FORECAST_HORIZON_DAYS` past *as_of*.

    Checked on the **resolved** window, so both window forms are bound by one
    rule, and against the case's ``as_of`` rather than a wall clock, so the limit
    a case is scored on is the limit it was generated under. A window this
    returns ``True`` for is a scope limit — ``not_available`` — never an argument
    fault: mixing the two would make the false-abstention metric uninterpretable
    (``decisions.md`` § Typed abstention).
    """
    return date.fromisoformat(end_date) > as_of + timedelta(days=FORECAST_HORIZON_DAYS)


def _transpose(daily: dict[str, list]) -> list[DailyWeatherRow]:
    """Turn Open-Meteo's parallel arrays into GR2L-ready daily rows."""
    times = daily.get("time", [])
    rows: list[DailyWeatherRow] = []
    for i, day in enumerate(times):
        values = {
            "tm": daily["temperature_2m_mean"][i],
            "tx": daily["temperature_2m_max"][i],
            "tn": daily["temperature_2m_min"][i],
            "rf": daily["relative_humidity_2m_mean"][i],
            "precip": daily["precipitation_sum"][i],
            "w": daily["wind_speed_10m_mean"][i],
            "gs": daily["shortwave_radiation_sum"][i],
        }
        missing = [name for name, val in values.items() if val is None]
        if missing:
            raise IncompleteWeatherError(
                f"Open-Meteo returned no data for {day}: "
                f"missing {', '.join(missing)}. Narrow the date window."
            )
        rows.append(
            DailyWeatherRow(
                Date=day,
                tm=values["tm"],
                tx=values["tx"],
                tn=values["tn"],
                rf=values["rf"],
                precip=values["precip"],
                w=values["w"],
                gs=values["gs"] * _RADIATION_MJ_TO_JCM2,
            )
        )
    return rows


def _upstream_error(response: httpx.Response) -> OpenMeteoError:
    """Turn a rejected response into an error carrying Open-Meteo's own ``reason``.

    Open-Meteo answers a bad request with ``{"error": true, "reason": "..."}``, which
    says precisely what was wrong ("Past days is invalid. Allowed range 0 to 93."). A
    bare status code does not, and the agent has no way to correct a window from it.
    """
    reason: str | None = None
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        reason = body.get("reason")
    return OpenMeteoError(reason or f"HTTP {response.status_code}")


def days_in_window(start_date: str, end_date: str) -> list[str]:
    """Every calendar day of the inclusive window, as ISO text, in order.

    The window's decomposition into the unit :class:`ArchiveWeatherClient` caches
    and assembles from (T146). Days rather than the window itself, because a
    window has two degrees of freedom and a rollout may reach one through
    ``past_days`` / ``forecast_days`` instead of dates — so the set of *windows* a
    case can produce is a plane, while the set of *days* those windows are made
    of is bounded by ``as_of`` and :data:`FORECAST_HORIZON_DAYS` and is therefore
    enumerable.
    """
    start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    if start > end:
        raise InvalidWindowError(f"start_date {start} is after end_date {end}.")
    return [(start + timedelta(days=offset)).isoformat() for offset in range((end - start).days + 1)]


def _contiguous_runs(days: Sequence[str]) -> list[tuple[str, str]]:
    """*days* grouped into maximal contiguous ``(first, last)`` spans, in order.

    What turns a list of missing days back into as few Archive requests as it can
    be answered in: a cold 31-day window is one request whose rows are then filed
    one per day, not 31 requests. Duplicates and unsorted input are tolerated —
    the caller's list is whatever the window produced.
    """
    ordered = sorted({date.fromisoformat(day) for day in days})
    runs: list[tuple[str, str]] = []
    for day in ordered:
        if runs and day == date.fromisoformat(runs[-1][1]) + timedelta(days=1):
            runs[-1] = (runs[-1][0], day.isoformat())
            continue
        runs.append((day.isoformat(), day.isoformat()))
    return runs


def _build_params(latitude: float, longitude: float, start_date: str, end_date: str) -> dict[str, object]:
    """The exact Open-Meteo query parameters :func:`fetch_daily_weather` sends.

    Shared with :class:`ArchiveWeatherClient`'s cache key so the key can never drift
    from what the client actually requests.
    """
    return {
        "latitude": latitude,
        "longitude": longitude,
        "daily": _DAILY_VARS,
        "timezone": "auto",
        "wind_speed_unit": "kmh",
        "start_date": start_date,
        "end_date": end_date,
    }


async def fetch_daily_weather(
    latitude: float,
    longitude: float,
    *,
    start_date: str,
    end_date: str,
    client: httpx.AsyncClient | None = None,
) -> WeatherResult:
    """Fetch daily weather for one point over an absolute window, as GR2L-ready rows.

    **One endpoint, no selection.** Archive (ERA5 reanalysis) is the only
    Open-Meteo backend this deployment uses: with the station serving every
    window the site's own record covers, nothing is left for the live Forecast
    endpoint to answer that Archive cannot, and Archive refuses none of it
    (`decisions.md` § Weather sources). So there is no backend argument, no
    cutoff, and no date parameter at all — this function no longer needs to know
    what day it is, which is the strongest form of the wall-clock guarantee
    ``site.py`` claims for it.

    The window is **always explicit**: callers resolve any relative form with
    :func:`resolve_window` first, so no request can inherit Open-Meteo's implicit
    seven-day forecast tail.

    *client* defaults to the module-level singleton when omitted;
    :class:`ArchiveWeatherClient` passes its own owned ``httpx.AsyncClient``
    instead of reaching into that shared singleton.

    Raises:
        OpenMeteoError: Open-Meteo rejected the request (carries its ``reason``).
        IncompleteWeatherError: a day in the window has no data for some variable.
    """
    params = _build_params(latitude, longitude, start_date, end_date)
    http_client = client if client is not None else _get_client()

    logger.debug("Fetching Open-Meteo weather", url=ARCHIVE_URL, params=params)
    response = await http_client.get(ARCHIVE_URL, params=params)
    if response.is_error:
        raise _upstream_error(response)
    payload = response.json()

    rows = _transpose(payload.get("daily", {}))
    # Open-Meteo's own `elevation` is deliberately dropped rather than carried:
    # it is the height of a ~1 km model cell, and the surveyed roof height is
    # `site.py`'s to state (`agent_architecture.md` §3.3).
    return WeatherResult(
        latitude=payload.get("latitude", latitude),
        longitude=payload.get("longitude", longitude),
        timezone=payload.get("timezone", "auto"),
        source=ARCHIVE_SOURCE,
        data=rows,
    )


def _assemble(days: Sequence[str], entries: Sequence[Any]) -> WeatherResult:
    """One window's :class:`WeatherResult` from its per-day cache entries.

    The provenance comes from the first day and the rows from all of them, in the
    window's own order. The day list is then checked against the rows rather than
    trusted: an entry holding a row for some other day, or none at all, would
    otherwise assemble into a window that is quietly short or quietly shifted,
    and every consumer downstream — GR2L's forcing, the irrigation seed, a plot —
    reads these rows positionally against the window it asked for.
    """
    results = [WeatherResult.model_validate(entry) for entry in entries]
    rows = [row for result in results for row in result.data]
    if [row.Date for row in rows] != list(days):
        raise IncompleteWeatherError(
            f"The cached days do not reconstruct {days[0]}..{days[-1]}: got "
            f"{[row.Date for row in rows]}."
        )
    first = results[0]
    return WeatherResult(
        latitude=first.latitude,
        longitude=first.longitude,
        timezone=first.timezone,
        source=first.source,
        data=rows,
    )


@runtime_checkable
class WeatherClient(Protocol):
    """Layer-2 weather source, reached through ``ctx.weather``.

    Every consumer — the standalone tool, GR2L, the irrigation calculator, the
    plot tool — resolves an absolute window through one object implementing this,
    and sees the same rows, units and day boundary regardless of which concrete
    source answered (`agent_architecture.md` §3.3).
    :class:`CompositeWeatherClient` is what a context actually holds;
    :class:`ArchiveWeatherClient` is its Open-Meteo half.
    """

    async def fetch(self, *, start_date: str, end_date: str) -> WeatherResult:
        """Daily weather over an absolute ``[start_date, end_date]`` window."""
        ...


class ArchiveWeatherClient:
    """Cached :data:`WeatherClient` implementation over Open-Meteo's Archive backend.

    Archive (ERA5 reanalysis) is deterministic in its window and therefore the one
    Open-Meteo path worth caching; since T045 it is also the only one there is, so
    this client selects nothing and reads no clock. Owns one ``httpx.AsyncClient``
    for its own lifetime instead of reaching into :func:`fetch_daily_weather`'s
    module-level singleton, the same construction-seam move
    :mod:`agents.text_to_sql.executor`-style factories make for the database
    (`decisions.md` § The construction seam).

    **The cached unit is the day, not the window** (T146). Each entry is the
    canonical one-day Archive request and the row it answers with; a window is
    the days between its ends, looked up together through
    :meth:`~..cache.ResponseCache.fetch_many` and assembled back into one
    :class:`WeatherResult`. The window was the unit until the measurement run
    priced it: a rollout reaches a window through ``past_days`` /
    ``forecast_days`` as readily as through dates, so the reachable *windows* are
    a plane over two axes and a capture pass can only ever have warmed a line
    through it — which is what left forward-facing templates losing most of their
    rollouts to replay misses on windows one day either side of gold's. The
    reachable *days* are the band ``as_of`` ± the forecast horizon, which is a
    list. §5 licenses the change by naming what this cache is load-bearing for:
    GR2L, whose determinism it carries and whose key stays the whole request;
    for weather it is cost and speed, Archive being re-fetchable indefinitely.
    The day is a sound unit empirically as well as by construction — re-keyed,
    the 1677 committed window entries agreed on every one of the 439 days they
    overlap on, with no conflict (``findings.md``).

    A hit returns straight from *cache*, issuing no request at all. A miss goes
    out **in contiguous runs**, so a cold 31-day window costs one request and
    lands as 31 reusable entries — see ``decisions.md`` § The response cache. No
    canary is required here: unlike GR2L, Archive carries no reproducibility
    claim (`agent_architecture.md` §5).

    The three cache modes are the two arguments' four useful combinations minus
    one. *cache* may be ``None`` — **off**: fetch the whole window live every
    time, record nothing, and never split it into days, there being nothing to
    file them under. That is production's binding: the running service has no
    case, and a committed entry keyed on an absolute day would keep serving the
    *forecast* a day once returned after that day had become an observation.
    With a cache and ``allow_live=True`` this **records**; with
    ``allow_live=False`` it **replays**, and a day it does not hold is a hard
    :class:`~..cache.CacheMissError` rather than a quiet call out. The fourth
    combination — replay with no cache — has nothing to replay from and raises at
    construction.
    """

    def __init__(
        self,
        cache: ResponseCache | None,
        *,
        allow_live: bool = True,
        latitude: float = SITE_LATITUDE,
        longitude: float = SITE_LONGITUDE,
    ) -> None:
        if cache is None and not allow_live:
            raise ValueError("Replay (allow_live=False) needs a cache to replay from.")
        self._cache = cache
        self._allow_live = allow_live
        self._latitude = latitude
        self._longitude = longitude
        self._http_client = httpx.AsyncClient(timeout=_TIMEOUT_SECONDS)

    def day_request(self, day: str) -> dict[str, Any]:
        """The canonical request one cached *day* is keyed on.

        Exactly the request that would fetch that day alone, so the key stays a
        function of what the client sends rather than of a schema invented beside
        it — the property ``cache.py`` refuses to give up when it batches. It is
        public because the capture pass and the migration that re-keyed the
        committed windows both have to name a day the same way this does.
        """
        return {
            "url": ARCHIVE_URL,
            "params": _build_params(self._latitude, self._longitude, day, day),
        }

    async def fetch(self, *, start_date: str, end_date: str) -> WeatherResult:
        if self._cache is None:
            return await self._fetch_window(start_date, end_date)

        days = days_in_window(start_date, end_date)
        entries = await self._cache.fetch_many(
            [self.day_request(day) for day in days],
            self._fill,
            allow_live=self._allow_live,
        )
        return _assemble(days, entries)

    async def _fetch_window(self, start_date: str, end_date: str) -> WeatherResult:
        """One live Archive request over an absolute window, through this client's socket."""
        return await fetch_daily_weather(
            self._latitude,
            self._longitude,
            start_date=start_date,
            end_date=end_date,
            client=self._http_client,
        )

    async def _fill(self, missing: Sequence[Any]) -> list[dict[str, Any]]:
        """Fetch every missing day, one request per contiguous run, in the order asked.

        Each returned entry is a one-day :class:`WeatherResult`: the window
        response's own provenance — the grid cell Archive answered from, its
        timezone, the source stamp — carried onto the single row, so a day
        assembled back into any other window is indistinguishable from one
        fetched on its own.
        """
        wanted = [str(request["params"]["start_date"]) for request in missing]
        filled: dict[str, dict[str, Any]] = {}
        for first, last in _contiguous_runs(wanted):
            result = await self._fetch_window(first, last)
            for row in result.data:
                filled[row.Date] = result.model_copy(update={"data": [row]}).model_dump()

        absent = [day for day in wanted if day not in filled]
        if absent:
            raise IncompleteWeatherError(
                f"Open-Meteo returned no row for {', '.join(sorted(set(absent)))}. "
                "Narrow the date window."
            )
        return [filled[day] for day in wanted]


class CompositeWeatherClient:
    """The :data:`WeatherClient` a context holds: station where it reaches, Archive else.

    Source resolution lives **here**, at layer 2, and not in
    :func:`fetch_daily_weather`, because the station reads the case's database
    handle and the pure layer has no channel to a context
    (``agent_architecture.md`` §3.3). Every consumer — the standalone tool, GR2L,
    later the irrigation calculator and the plot tool — goes through one of these
    and therefore sees one provenance per window.

    The rule is whole-window or nothing: the station serves only if it can
    derive a complete day for **every** day asked for, tested through the as-of
    view it was constructed with. A window the record covers only partly, and
    every window reaching past ``as_of`` — where the view has nothing at all —
    falls to the Archive **whole**. Mixing them per day would make a case's
    forcing a per-day blend of two instruments with a measured offset between
    them, and the station's biases undisclosable as one property of one source
    (``decisions.md`` § Weather sources).

    **A station window never touches the response cache**, and that is
    structural rather than a rule to remember: the coverage test runs first, and
    the Archive half — the only thing here holding a cache — is reached only
    once it has failed. So no key is computed, no entry recorded and no miss
    raised for a station window, in any of the three cache modes. Nothing is
    lost by it: the station is a pure function of the pinned database, which the
    ``water.duckdb`` hash already covers, so an entry would be a second copy of
    something already pinned (``agent_architecture.md`` §5).
    """

    def __init__(self, station: StationWeatherSource, archive: WeatherClient) -> None:
        self._station = station
        self._archive = archive

    async def fetch(self, *, start_date: str, end_date: str) -> WeatherResult:
        start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
        rows = await asyncio.to_thread(self._station.daily_rows, start, end)

        if len(rows) == (end - start).days + 1:
            logger.debug("Serving weather from the station", window=(start_date, end_date))
            return WeatherResult(
                latitude=SITE_LATITUDE,
                longitude=SITE_LONGITUDE,
                timezone=SITE_TIMEZONE,
                source=STATION_SOURCE,
                data=rows,
            )

        logger.debug(
            "Station record does not cover the window; falling to Archive whole",
            window=(start_date, end_date),
            station_days=len(rows),
        )
        return await self._archive.fetch(start_date=start_date, end_date=end_date)


def make_weather_client(
    db: ReadOnlyWarehouseQuery,
    cache: ResponseCache | None,
    *,
    allow_live: bool = True,
) -> WeatherClient:
    """Build the composite for one context — the ``weather_client_factory`` of §4.

    Both halves, in this order: the station reads the as-of views through *db*,
    the Archive half reads *cache*. A factory taking only *db* could not build
    the composite, which is why the seam passes both.

    *allow_live* reaches only the Archive half, because it is the only half that
    can call out at all. A replay pass (``allow_live=False``) therefore still
    answers every station window in full, from the pinned database, with no
    cache entry behind it.
    """
    return CompositeWeatherClient(
        StationWeatherSource(db), ArchiveWeatherClient(cache, allow_live=allow_live)
    )
