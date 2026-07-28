"""Pure async Open-Meteo client that emits GR2L-ready daily weather rows.

No ADK imports — this is the reusable seam behind both ``get_weather_forecast_tool``
and ``predict_green_roof_water_balance_tool``. It fetches the seven daily variables
GR2L needs, transposes Open-Meteo's column-oriented response into row-oriented
:class:`DailyWeatherRow` objects, and applies the two unit conversions GR2L's math
requires (``gs = shortwave_radiation_sum × 100`` to J/cm²/day; wind requested in
km/h). See ``weather_tool.md`` for the conversion rationale.
"""

from datetime import date, timedelta

import httpx
import structlog

from water_assistant_agent.assistant.tools.schemas import DailyWeatherRow, WeatherResult
from water_assistant_agent.assistant.tools.site import site_now

logger = structlog.get_logger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

# The Forecast backend reaches ~92 days into the past via ``past_days``; older
# windows must use the Archive backend.
_FORECAST_PAST_LIMIT_DAYS = 92

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


class _ClientHolder:
    """Module-level singleton holder for the shared httpx client."""

    instance: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Lazily create and cache a module-level ``httpx.AsyncClient``."""
    if _ClientHolder.instance is None:
        _ClientHolder.instance = httpx.AsyncClient(timeout=_TIMEOUT_SECONDS)
    return _ClientHolder.instance


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
            raise ValueError(
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


async def fetch_daily_weather(
    latitude: float,
    longitude: float,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    past_days: int | None = None,
    forecast_days: int | None = None,
) -> WeatherResult:
    """Fetch daily weather for one point as GR2L-ready rows.

    Raises on HTTP or parse errors (the ADK tool wrappers catch and convert to an
    ``ErrorResult``). Backend is chosen automatically: Archive for windows older
    than ~92 days, otherwise Forecast (with ``past_days`` / ``forecast_days``).
    """
    backend = _choose_backend(start_date)
    url = ARCHIVE_URL if backend == "archive" else FORECAST_URL

    params: dict[str, object] = {
        "latitude": latitude,
        "longitude": longitude,
        "daily": _DAILY_VARS,
        "timezone": "auto",
        "wind_speed_unit": "kmh",
    }
    if start_date is not None:
        params["start_date"] = start_date
    if end_date is not None:
        params["end_date"] = end_date
    if backend == "forecast":
        if past_days is not None:
            params["past_days"] = past_days
        if forecast_days is not None:
            params["forecast_days"] = forecast_days

    logger.debug("Fetching Open-Meteo weather", backend=backend, url=url, params=params)
    response = await _get_client().get(url, params=params)
    response.raise_for_status()
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
