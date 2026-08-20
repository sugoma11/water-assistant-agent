"""ADK tool: daily weather for the facility, from whichever source covers the window.

Thin wrapper over ``ctx.weather``, exposed as a standalone agent tool (reusable
without GR2L). The location is pinned to the site (see :mod:`site`), so the agent
supplies only a date window. Returns GR2L-ready daily rows so the same output can
be fed straight into the green-roof water-balance tool.

This wrapper is **layer 1 and nothing else**: it resolves the window against
``ctx.as_of``, checks the one scope limit, and hands an absolute window to the
client. Which source answers is layer 2's, decided from the window alone by the
composite behind ``ctx.weather`` (``agent_architecture.md`` §3.3) — the agent
names a window and a question, never a provenance.

The tool is produced by :func:`make_weather_forecast_tool`, which closes over one
:class:`~water_assistant_agent.assistant.context.ScenarioContext`: the window is
resolved against ``ctx.as_of``, never a wall clock of this module's own, so a
case frozen at its ``as_of`` and production's advancing site clock take the same
path (``agent_architecture.md`` §4). Docstring and signature are the ones ADK
turns into the tool declaration and are therefore candidate-optimizable text —
:func:`..toolset.build_toolset` rewrites ``__doc__`` on the produced callable.
"""

from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import structlog
from google.adk.tools.tool_context import ToolContext

from water_assistant_agent.assistant.tools.schemas import ErrorResult, NotAvailableResult
from water_assistant_agent.assistant.tools.series import (
    MAX_SERIES_DAYS,
    WEEK_DAYS,
    weather_periods,
)
from water_assistant_agent.assistant.tools.site import (
    SITE_ELEVATION_M,
    SITE_LATITUDE,
    SITE_LONGITUDE,
)
from water_assistant_agent.assistant.tools.weather_client import (
    FORECAST_HORIZON_DAYS,
    InvalidWindowError,
    WeatherFetchError,
    beyond_horizon,
    resolve_window,
)

if TYPE_CHECKING:
    from water_assistant_agent.assistant.context import ScenarioContext

logger = structlog.get_logger(__name__)

WeatherForecastTool = Callable[..., Awaitable[dict[str, Any]]]
"""What :func:`make_weather_forecast_tool` returns: the ADK-facing weather tool."""


def make_weather_forecast_tool(ctx: "ScenarioContext") -> WeatherForecastTool:
    """Build ``get_weather_forecast_tool`` bound to *ctx*.

    The returned callable keeps the exact name, signature and docstring of the
    tool the root instruction names, because ADK derives the declaration from the
    function itself; only the clock the window resolves against is bound here.
    That clock is also what the forecast horizon is measured from: a window
    ending more than :data:`~..tools.weather_client.FORECAST_HORIZON_DAYS` days
    past ``ctx.as_of`` is this tool's single typed ``not_available``.

    Rows come from ``ctx.weather`` — the composite that picks the station or the
    Archive from the window alone — so this wrapper never chooses a provenance
    and never names one. It reports whichever source answered, under ``source``.
    """

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
            past_days: Number of **complete past days** to include, ending yesterday.
                It adds no forecast days: ask for ``past_days=7`` and you get last
                week's observations only. Nothing bounds how far back it may reach.
            forecast_days: Number of days from **today** forward to include (0-16).
                Combine it with ``past_days`` to span both sides of today; with
                neither given, the window is the coming 7 days. Nothing is
                available more than 16 days ahead.

        Returns:
            dict: on success ``status='success'`` with the site's
            ``latitude``/``longitude``/``elevation`` (m), ``timezone``, ``source``,
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

            ``source`` says where the whole window came from and is chosen
            automatically, never by you: ``'station'`` means the site's own
            instruments — **say so in the answer** — and ``'archive'`` means a
            reanalysis of the wider area.

            A window longer than 31 days comes back with ``truncated: true``, an
            empty ``data``, and instead a ``summary`` over the whole window plus
            ``weekly`` aggregates — same fields, one row per week. Answer from
            those; do not re-request the window in pieces to get the days back.

            When the window ends more than 16 days ahead, ``status='not_available'``
            with a ``reason`` to pass on to the user: no weather exists that far
            out, so that is a scope limit, not a malfunction — say so instead of
            retrying with different arguments. On failure ``status='error'`` with
            ``error_details`` and an ``error_type``: ``'invalid_argument'`` means
            the call itself was wrong and can be corrected and retried,
            ``'upstream'`` means something the tool depends on failed — report the
            system-side problem rather than retrying.
        """
        # Resolved before the fetch: Open-Meteo's own past_days silently appends a
        # seven-day forecast tail, which would land in `data` unlabelled. The day
        # it resolves against is `ctx.as_of`'s, read per call — never a wall clock.
        today = ctx.as_of.date()
        try:
            window_start, window_end = resolve_window(
                start_date, end_date, past_days, forecast_days, today=today
            )
        except InvalidWindowError as exc:
            logger.info("Rejected weather window", error=str(exc))
            return ErrorResult(
                error_type="invalid_argument", error_details=str(exc)
            ).model_dump()

        # The one scope limit this tool signals. Nothing upstream enforces it —
        # the reanalysis serves any date it holds — so a window past the horizon
        # would otherwise come back looking like an ordinary answer.
        if beyond_horizon(window_end, today):
            logger.info(
                "Weather window reaches past the forecast horizon",
                window=(window_start, window_end),
                as_of=today.isoformat(),
            )
            return NotAvailableResult(
                reason=(
                    f"Weather is only available up to {FORECAST_HORIZON_DAYS} days ahead "
                    f"(through {today + timedelta(days=FORECAST_HORIZON_DAYS):%Y-%m-%d}), and "
                    f"the window asked for ends {window_end}."
                )
            ).model_dump()

        try:
            result = await ctx.weather.fetch(start_date=window_start, end_date=window_end)
        except WeatherFetchError as exc:
            # Upstream said what was wrong; passing it on lets the agent fix the window.
            logger.warning("Weather fetch rejected", window=(window_start, window_end), error=str(exc))
            return ErrorResult(error_type="upstream", error_details=str(exc)).model_dump()
        except Exception:
            logger.exception("Weather fetch failed")
            return ErrorResult(
                error_type="upstream",
                error_details=(
                    f"Failed to fetch weather for {window_start}..{window_end}. "
                    "Please try a different date window."
                ),
            ).model_dump()
        # Bounded here, at layer 1, and not in the client: the cap is on what the
        # model reads back, and GR2L consumes the same client's rows in full.
        if len(result.data) > MAX_SERIES_DAYS:
            logger.info(
                "Truncating the daily weather series",
                window=(window_start, window_end),
                days=len(result.data),
            )
            result = result.model_copy(
                update={
                    "truncated": True,
                    "summary": weather_periods(result.data, len(result.data))[0],
                    "weekly": weather_periods(result.data, WEEK_DAYS),
                    "data": [],
                }
            )

        # The site's own coordinates and surveyed height, not the source's. Only
        # `elevation` is *composed* here rather than overwritten: `WeatherResult`
        # does not carry one, because no weather source can know the height of a
        # roof — Open-Meteo reports its ~1 km cell and the station its mast. GR2L
        # takes `hoehe_nn` from the same `site.py` through its own wrapper, so the
        # two never disagree (`agent_architecture.md` §3.3).
        payload = result.model_dump()
        payload.update(
            {
                "latitude": SITE_LATITUDE,
                "longitude": SITE_LONGITUDE,
                "elevation": SITE_ELEVATION_M,
            }
        )
        return payload

    # ADK reads `__name__`; the qualname is reset so a `<locals>`-qualified name
    # never surfaces in logs or reprs (as in ``warehouse.make_query_database_tool``).
    get_weather_forecast_tool.__qualname__ = "get_weather_forecast_tool"
    return get_weather_forecast_tool
