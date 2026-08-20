"""Pure async Open-Meteo client that emits GR2L-ready daily weather rows.

No ADK imports — this is the reusable seam behind both ``get_weather_forecast_tool``
and ``predict_green_roof_water_balance_tool``. It fetches the seven daily variables
GR2L needs, transposes Open-Meteo's column-oriented response into row-oriented
:class:`DailyWeatherRow` objects, and applies the two unit conversions GR2L's math
requires (``gs = shortwave_radiation_sum × 100`` to J/cm²/day; wind requested in
km/h). See ``weather_tool.md`` for the conversion rationale.

:func:`fetch_daily_weather` takes an **absolute** window only. Relative windows are
resolved by :func:`resolve_window` in the tool wrappers, before the client is ever
called, because Open-Meteo's own ``past_days`` silently carries a seven-day forecast
tail: ``past_days=5`` returns five past days *plus* today and six forecast days, and
the rows are indistinguishable once transposed.
"""

from datetime import date, timedelta
from typing import Protocol, runtime_checkable

import httpx
import structlog

from water_assistant_agent.assistant.cache import ResponseCache
from water_assistant_agent.assistant.tools.schemas import DailyWeatherRow, WeatherResult
from water_assistant_agent.assistant.tools.site import SITE_LATITUDE, SITE_LONGITUDE, site_now

logger = structlog.get_logger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

# The Forecast backend reaches ~92 days into the past via ``past_days``; older
# windows must use the Archive backend.
_FORECAST_PAST_LIMIT_DAYS = 92

# Argument limits for the relative window form (Open-Meteo's own documented range).
_MAX_PAST_DAYS = 92
_MAX_FORECAST_DAYS = 16

# Open-Meteo's default when a Forecast request names no window at all; kept so a
# bare call still means "the coming week" after resolution.
_DEFAULT_FORECAST_DAYS = 7

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

    def __init__(self, reason: str, *, backend: str) -> None:
        super().__init__(f"Open-Meteo ({backend}) rejected the request: {reason}")
        self.reason = reason
        self.backend = backend


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


def _validate_count(value: int, field: str, maximum: int) -> int:
    """Bounds-check one relative-window count."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise InvalidWindowError(f"{field} must be a whole number of days, got {value!r}.")
    if not 0 <= value <= maximum:
        raise InvalidWindowError(f"{field} must be between 0 and {maximum}, got {value}.")
    return value


def resolve_window(
    start_date: str | None = None,
    end_date: str | None = None,
    past_days: int | None = None,
    forecast_days: int | None = None,
    *,
    today: date | None = None,
) -> tuple[str, str]:
    """Resolve either window form to one absolute ``(start, end)`` pair of ISO dates.

    Both tool wrappers call this before touching the client, so the client only ever
    sees an explicit window. That is what keeps a past-only request past-only:
    Open-Meteo defaults ``forecast_days`` to 7, so forwarding a bare ``past_days``
    appends a week of *predictions* to what the user asked to be history, with
    nothing in the returned rows to mark which is which.

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

    Raises:
        InvalidWindowError: mixed window forms, a half-given explicit window, an
            unparseable date, an out-of-range count, a reversed range, or a pair
            of counts that selects no days at all.
    """
    today = today or site_now().date()
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
        past = 0 if past_days is None else _validate_count(past_days, "past_days", _MAX_PAST_DAYS)
        if past_days is None and forecast_days is None:
            future = _DEFAULT_FORECAST_DAYS
        else:
            future = (
                0
                if forecast_days is None
                else _validate_count(forecast_days, "forecast_days", _MAX_FORECAST_DAYS)
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


def _choose_backend(start_date: str | None) -> str:
    """Pick the Archive backend for windows older than the Forecast reach.

    The cutoff is measured from *today at the site*, the same clock the agent is
    given, so a window it derives from that date can't fall a day outside the
    Forecast reach here.
    """
    if start_date is not None:
        cutoff = site_now().date() - timedelta(days=_FORECAST_PAST_LIMIT_DAYS)
        if date.fromisoformat(start_date) < cutoff:
            return "archive"
    return "forecast"


def _transpose(daily: dict[str, list], backend: str) -> list[DailyWeatherRow]:
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
                f"Open-Meteo ({backend}) returned no data for {day}: "
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


def _upstream_error(response: httpx.Response, backend: str) -> OpenMeteoError:
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
    return OpenMeteoError(reason or f"HTTP {response.status_code}", backend=backend)


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
    force_archive: bool = False,
) -> WeatherResult:
    """Fetch daily weather for one point over an absolute window, as GR2L-ready rows.

    The window is **always explicit**: callers resolve any relative form with
    :func:`resolve_window` first, so no request can inherit Open-Meteo's implicit
    seven-day forecast tail. Backend is chosen automatically — Archive for windows
    older than ~92 days, otherwise Forecast — unless *force_archive* selects Archive
    outright, which :class:`ArchiveWeatherClient` uses to stay wall-clock-free.

    *client* defaults to the module-level singleton when omitted, so existing
    callers are unaffected; :class:`ArchiveWeatherClient` passes its own owned
    ``httpx.AsyncClient`` instead of reaching into that shared singleton.

    Raises:
        OpenMeteoError: the backend rejected the request (carries its ``reason``).
        IncompleteWeatherError: a day in the window has no data for some variable.
    """
    backend = "archive" if force_archive else _choose_backend(start_date)
    url = ARCHIVE_URL if backend == "archive" else FORECAST_URL
    params = _build_params(latitude, longitude, start_date, end_date)
    http_client = client if client is not None else _get_client()

    logger.debug("Fetching Open-Meteo weather", backend=backend, url=url, params=params)
    response = await http_client.get(url, params=params)
    if response.is_error:
        raise _upstream_error(response, backend)
    payload = response.json()

    rows = _transpose(payload.get("daily", {}), backend)
    return WeatherResult(
        latitude=payload.get("latitude", latitude),
        longitude=payload.get("longitude", longitude),
        elevation=payload.get("elevation", 0.0),
        timezone=payload.get("timezone", "auto"),
        backend=backend,
        data=rows,
    )


@runtime_checkable
class WeatherClient(Protocol):
    """Layer-2 weather source, reached through ``ctx.weather``.

    Every consumer — the standalone tool, GR2L, the irrigation calculator, the
    plot tool — resolves an absolute window through one object implementing this,
    and sees the same rows, units and day boundary regardless of which concrete
    source answered (`agent_architecture.md` §3.3). :class:`ArchiveWeatherClient`
    is the first implementation; the composite adding the station half is a later
    packet.
    """

    async def fetch(self, *, start_date: str, end_date: str) -> WeatherResult:
        """Daily weather over an absolute ``[start_date, end_date]`` window."""
        ...


class ArchiveWeatherClient:
    """Cached :data:`WeatherClient` implementation over Open-Meteo's Archive backend.

    Always resolves through Archive (ERA5 reanalysis) — deterministic in
    ``(rows, parameters)`` and therefore the one Open-Meteo path safe to cache — and
    never touches the wall clock in doing so. Owns one ``httpx.AsyncClient`` for its
    own lifetime instead of reaching into :func:`fetch_daily_weather`'s module-level
    singleton, the same construction-seam move
    :mod:`agents.text_to_sql.executor`-style factories make for the database
    (`decisions.md` § The construction seam).

    A hit returns straight from *cache*, issuing no request at all. A miss fetches
    live via :func:`fetch_daily_weather` (``force_archive=True``) and records it,
    keyed on exactly the query parameters sent — see
    ``decisions.md`` § The response cache. No canary is required here: unlike GR2L,
    Archive's cache is load-bearing only for cost and speed, and carries no
    reproducibility claim (`agent_architecture.md` §5).
    """

    def __init__(
        self,
        cache: ResponseCache,
        *,
        latitude: float = SITE_LATITUDE,
        longitude: float = SITE_LONGITUDE,
    ) -> None:
        self._cache = cache
        self._latitude = latitude
        self._longitude = longitude
        self._http_client = httpx.AsyncClient(timeout=_TIMEOUT_SECONDS)

    async def fetch(self, *, start_date: str, end_date: str) -> WeatherResult:
        canonical_request = {
            "url": ARCHIVE_URL,
            "params": _build_params(self._latitude, self._longitude, start_date, end_date),
        }

        async def live_fetch() -> dict[str, object]:
            result = await fetch_daily_weather(
                self._latitude,
                self._longitude,
                start_date=start_date,
                end_date=end_date,
                client=self._http_client,
                force_archive=True,
            )
            return result.model_dump()

        cached = await self._cache.fetch(canonical_request, live_fetch)
        return WeatherResult.model_validate(cached)
