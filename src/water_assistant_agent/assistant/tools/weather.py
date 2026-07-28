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
from water_assistant_agent.assistant.tools.weather_client import fetch_daily_weather

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
    wind speed, and global radiation for each day — already unit-converted for
    the green-roof water-balance model. The location is the facility itself and is
    fixed, so only the date window has to be given. Use it on its own when the
    user asks about the weather; the green-roof tool fetches its own weather.

    Args:
        start_date: Optional window start, ``YYYY-MM-DD``. With ``end_date``,
            selects an explicit date range (used for historical windows).
        end_date: Optional window end, ``YYYY-MM-DD``.
        past_days: Optional number of recent past days to include (0-92).
        forecast_days: Optional number of future days to include (0-16).

    Returns:
        dict: on success ``status='success'`` with the site's
        ``latitude``/``longitude``/``elevation``, ``timezone``, ``backend``, and
        ``data`` (one daily row per day). On failure ``status='error'`` with
        ``error_details``.
    """
    try:
        result = await fetch_daily_weather(
            SITE_LATITUDE,
            SITE_LONGITUDE,
            start_date=start_date,
            end_date=end_date,
            past_days=past_days,
            forecast_days=forecast_days,
        )
    except Exception:
        logger.exception("Weather fetch failed")
        return ErrorResult(
            error_details="Failed to fetch weather. Please try a different date window."
        ).model_dump()
    # Report the site's own coordinates/height rather than Open-Meteo's grid cell.
    return result.model_copy(
        update={
            "latitude": SITE_LATITUDE,
            "longitude": SITE_LONGITUDE,
            "elevation": SITE_ELEVATION_M,
        }
    ).model_dump()
