"""ADK tool: daily weather forecast / archive for the facility.

Thin wrapper over :func:`weather_client.fetch_daily_weather`, exposed as a
standalone agent tool (reusable without GR2L). The location is pinned to the
site (see :mod:`site`), so the agent supplies only a date window. Returns
GR2L-ready daily rows so the same output can be fed straight into the green-roof
water-balance tool.
"""

from typing import Any

import structlog
from google.adk.tools.tool_context import ToolContext

from water_assistant_agent.assistant.tools.schemas import ErrorResult
from water_assistant_agent.assistant.tools.site import (
    SITE_ELEVATION_M,
    SITE_LATITUDE,
    SITE_LONGITUDE,
)
from water_assistant_agent.assistant.tools.weather_client import (
    InvalidWindowError,
    WeatherFetchError,
    fetch_daily_weather,
    resolve_window,
)

logger = structlog.get_logger(__name__)


async def get_weather_forecast_tool(
    start_date: str | None = None,
    end_date: str | None = None,
    past_days: int | None = None,
    forecast_days: int | None = None,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Fetch daily weather (past and/or forecast) for the research facility.

    Returns the daily mean/max/min temperature, relative humidity, precipitation,
    wind speed, and global radiation for each day. The location is the facility
    itself and is fixed, so only the date window has to be given. Use it on its
    own when the user asks about the weather; the green-roof tool fetches its own
    weather.

    Every value comes back in the unit named under ``Returns`` below. Two of them
    are **not** the unit normally assumed — wind is km/h, not m/s, and radiation
    is J/cm²/day, not W/m². State the unit given; never convert or guess one.

    Args:
        start_date: Window start, ``YYYY-MM-DD``. Give it together with
            ``end_date`` to select an explicit date range (used for historical
            windows).
        end_date: Window end, ``YYYY-MM-DD``. Required whenever ``start_date`` is
            given.
        past_days: Number of **complete past days** to include, ending yesterday
            (0-92). It adds no forecast days: ask for ``past_days=7`` and you get
            last week's observations only.
        forecast_days: Number of days from **today** forward to include (0-16).
            Combine it with ``past_days`` to span both sides of today; with
            neither given, the window is the coming 7 days.

    Returns:
        dict: on success ``status='success'`` with the site's
        ``latitude``/``longitude``/``elevation`` (m), ``timezone``, ``backend``,
        and ``data`` — one row per day, each carrying the day's date and these
        seven values under short keys:

        * ``Date`` — the day, ``YYYY-MM-DD``
        * ``tm`` — mean temperature, **°C**
        * ``tx`` — maximum temperature, **°C**
        * ``tn`` — minimum temperature, **°C**
        * ``rf`` — mean relative humidity, **%**
        * ``precip`` — precipitation total, **mm**
        * ``w`` — mean wind speed, **km/h** (10 m above ground)
        * ``gs`` — global (shortwave) radiation total, **J/cm²/day**

        On failure ``status='error'`` with ``error_details``.
    """
    # Resolved before the fetch: Open-Meteo's own past_days silently appends a
    # seven-day forecast tail, which would land in `data` unlabelled.
    try:
        window_start, window_end = resolve_window(start_date, end_date, past_days, forecast_days)
    except InvalidWindowError as exc:
        logger.info("Rejected weather window", error=str(exc))
        return ErrorResult(error_details=str(exc)).model_dump()

    try:
        result = await fetch_daily_weather(
            SITE_LATITUDE,
            SITE_LONGITUDE,
            start_date=window_start,
            end_date=window_end,
        )
    except WeatherFetchError as exc:
        # Upstream said what was wrong; passing it on lets the agent fix the window.
        logger.warning("Weather fetch rejected", window=(window_start, window_end), error=str(exc))
        return ErrorResult(error_details=str(exc)).model_dump()
    except Exception:
        logger.exception("Weather fetch failed")
        return ErrorResult(
            error_details=(
                f"Failed to fetch weather for {window_start}..{window_end}. "
                "Please try a different date window."
            )
        ).model_dump()
    # Report the site's own coordinates/height rather than Open-Meteo's grid cell.
    return result.model_copy(
        update={
            "latitude": SITE_LATITUDE,
            "longitude": SITE_LONGITUDE,
            "elevation": SITE_ELEVATION_M,
        }
    ).model_dump()
