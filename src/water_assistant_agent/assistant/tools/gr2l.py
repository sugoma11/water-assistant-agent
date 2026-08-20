"""ADK tool: green-roof daily water balance via the GR2L model.

Composes the weather client and the GR2L client so the agent can answer
green-roof hydrology questions (stormwater retention, runoff, substrate moisture
/ drought stress, evapotranspiration) from a date window + roof type in a single
call. The location is pinned to the facility (see :mod:`site`). Weather is
fetched and unit-converted in code — the LLM never shuttles daily arrays or
performs the fragile conversions itself.

Mind the layering, because the two halves say opposite things (``gr2l_tool.md``):
the GR2L *endpoint* requires its caller to supply every daily weather row, and
this module is that caller. The agent-facing docstring below therefore states the
reverse — that the tool fetches its own weather, so the agent must not call the
weather tool first. Do not "fix" one to match the other.

Units are the other thing this layer owns: GR2L speaks substrate storage in mm,
the sensors and the researchers speak %θ, so the conversion happens here (via
:mod:`swc`) in both directions and never in the model's head.

The tool is produced by :func:`make_green_roof_balance_tool`, which closes over one
:class:`~water_assistant_agent.assistant.context.ScenarioContext`: the window
resolves against ``ctx.clock()`` and the soil-moisture seed is read through
``ctx.db``, so a case sees its own as-of views and its own day, and neither is a
module-level singleton (``agent_architecture.md`` §4).
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date
from typing import TYPE_CHECKING, Any

import structlog
from google.adk.tools.tool_context import ToolContext

from water_assistant_agent.assistant.ports import ReadOnlyWarehouseQuery
from water_assistant_agent.assistant.tools.gr2l_client import (
    NON_MODELLABLE_ROOFS,
    ROOF_PRESETS,
    Gr2lConfigError,
    resolve_roof_parameters,
    run_gr2l,
)
from water_assistant_agent.assistant.tools.schemas import (
    DailyWeatherRow,
    ErrorResult,
    GreenRoofBalanceResult,
    GreenRoofDay,
    GreenRoofSummary,
    Gr2lResultRow,
    NotAvailableResult,
    RoofParameters,
    SwcSeed,
)
from water_assistant_agent.assistant.tools.site import (
    SITE_ELEVATION_M,
    SITE_LATITUDE,
    SITE_LONGITUDE,
)
from water_assistant_agent.assistant.tools.swc import (
    MM_ONLY_ROOFS,
    SwcUnavailableError,
    latest_measured_swc,
    mm_to_theta_pct,
    theta_pct_to_mm,
)
from water_assistant_agent.assistant.tools.weather_client import (
    InvalidWindowError,
    WeatherFetchError,
    fetch_daily_weather,
    resolve_window,
)

if TYPE_CHECKING:
    from water_assistant_agent.assistant.context import ScenarioContext

logger = structlog.get_logger(__name__)

GreenRoofBalanceTool = Callable[..., Awaitable[dict[str, Any]]]
"""What :func:`make_green_roof_balance_tool` returns: the ADK-facing GR2L tool."""

# Fraction of the substrate storage range above Ssubmin below which the roof is
# considered water-stressed.
_DROUGHT_FRACTION = 0.05


def _to_days(
    result_rows: list[Gr2lResultRow],
    parameters: RoofParameters,
    roof_type: str,
) -> list[GreenRoofDay]:
    """Restate each GR2L day's mm storage as %θ, keeping the mm alongside."""
    mm_only = roof_type in MM_ONLY_ROOFS
    days: list[GreenRoofDay] = []
    for row in result_rows:
        swc_pct = (
            None
            if mm_only or row.Ssub is None
            else round(mm_to_theta_pct(row.Ssub, parameters.SH), 2)
        )
        days.append(GreenRoofDay(**row.model_dump(), swc_pct=swc_pct))
    return days


def _summarize(
    weather_rows: list[DailyWeatherRow],
    days: list[GreenRoofDay],
    parameters: RoofParameters,
) -> GreenRoofSummary:
    """Derive retention / drought indicators from the GR2L series."""
    total_precip = sum(row.precip for row in weather_rows)
    total_out = sum((row.OUT or 0.0) for row in days)
    retention = total_precip - total_out
    # Undefined rather than 0 % on a dry window: nothing fell to be retained.
    retention_pct = round(retention / total_precip * 100.0, 1) if total_precip > 0 else None

    # Day 1 only seeds the stores, so its OUT is null and counts as 0 — its rain
    # is scored as retained whether or not it ran off. Flag it rather than
    # quietly over-reporting (gr2l_tool.md, "Retention over a window").
    seed_day_was_wet = bool(weather_rows) and weather_rows[0].precip > 0

    # Ssub is tracked for all roof types (the wetland's storage mat included);
    # the None-guard only protects against older model builds returning null.
    storages = [row.Ssub for row in days if row.Ssub is not None]
    span = max(parameters.Ssubmax - parameters.Ssubmin, 0.0)
    drought_threshold = parameters.Ssubmin + _DROUGHT_FRACTION * span
    min_storage = min(storages) if storages else 0.0
    drought_stress = bool(storages) and min_storage <= drought_threshold
    min_swc = [row.swc_pct for row in days if row.swc_pct is not None]

    return GreenRoofSummary(
        days=len(days),
        total_precip_mm=round(total_precip, 2),
        total_outflow_mm=round(total_out, 2),
        retention_mm=round(retention, 2),
        retention_pct=retention_pct,
        min_substrate_storage_mm=round(min_storage, 2),
        min_swc_pct=min(min_swc) if min_swc else None,
        drought_stress=drought_stress,
        retention_excludes_seed_day_runoff=seed_day_was_wet,
    )


async def _resolve_seed(
    executor: ReadOnlyWarehouseQuery,
    roof_type: str,
    window_start: date,
    sh_cm: float,
    caller_swc_pct: float | None,
) -> SwcSeed:
    """Establish day 1's substrate state, in %θ and in the mm GR2L consumes.

    The caller's value wins when given; otherwise the roof's own sensor supplies
    it, read through *executor*. Propagates :class:`SwcUnavailableError` when
    neither exists — the tool never falls back to a made-up state.
    """
    if caller_swc_pct is not None:
        return SwcSeed(
            source="caller",
            swc_pct=caller_swc_pct,
            substrate_storage_mm=round(theta_pct_to_mm(caller_swc_pct, sh_cm), 3),
        )

    measured = await asyncio.to_thread(
        latest_measured_swc, executor, roof_type, window_start
    )
    return SwcSeed(
        source="measured",
        swc_pct=round(measured.theta_pct, 2),
        substrate_storage_mm=round(theta_pct_to_mm(measured.theta_pct, sh_cm), 3),
        measured_at=measured.measured_at.isoformat(sep=" "),
        age_days=measured.age_days,
        is_stale=measured.is_stale,
    )


def make_green_roof_balance_tool(ctx: "ScenarioContext") -> GreenRoofBalanceTool:
    """Build ``predict_green_roof_water_balance_tool`` bound to *ctx*.

    Two bindings, both read per call rather than captured here: ``ctx.clock()``
    resolves the window, and ``ctx.db`` — the case's as-of executor — supplies the
    soil-moisture seed, replacing the warehouse singleton this wrapper used to
    reach for (``findings.md`` § Codebase seams). Name, signature and docstring are
    the tool declaration ADK sends, so they stay identical to the production tool's;
    :func:`..toolset.build_toolset` is the one place ``__doc__`` is replaced, by a
    candidate.

    The weather half still goes through :func:`fetch_daily_weather` rather than
    ``ctx.weather``, for the reason :func:`..tools.weather.make_weather_forecast_tool`
    gives: today's only client is Archive-only, and T043's composite is what
    replaces it.
    """

    async def predict_green_roof_water_balance_tool(
        roof_type: str,
        start_date: str | None = None,
        end_date: str | None = None,
        past_days: int | None = None,
        forecast_days: int | None = None,
        initial_soil_moisture_pct: float | None = None,
        albedo: float | None = None,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Predict a green roof's daily water balance (moisture, runoff, ET) over a period.

        **Fetches its own weather and its own starting soil moisture** for the given
        date window — do not call the weather tool first, do not query the database
        for the roof's current moisture, and do not pass either in. It then runs the
        GR2L two-layer water-balance model to produce, for each day, the substrate
        water content, roof runoff, and evapotranspiration. Use it for
        stormwater-retention, roof-runoff, soil-moisture / drought-risk, or
        evapotranspiration-cooling questions over a sequence of days.

        Soil moisture is in **% volumetric water content (%θ)**, the same unit the
        sensors and the ops manual use, both in and out; millimetres of stored water
        appear alongside for the water balance. The **wetland** roof is the
        exception — it reports millimetres only, because water ponded above its mat
        has no %θ equivalent.

        Four roofs can be modelled: ``wetland``, ``non_irrigated_extensive``,
        ``irrigated_extensive``, ``semi_intensive``. The **gravel roof cannot be** —
        it has no substrate, so there is nothing for the model to simulate, and the
        tool reports that as ``status='not_available'``. Its measured sensor data is
        still available through the database.

        All roofs are segments of the same building, so no location is needed. Past
        and future windows are both supported and resolved automatically; just name
        the dates the question is about (up to 16 days ahead).

        Args:
            roof_type: One of ``wetland``, ``non_irrigated_extensive``,
                ``irrigated_extensive``, ``semi_intensive`` — selects the roof's
                physical parameters.
            start_date: Window start, ``YYYY-MM-DD`` (give ``end_date`` with it).
            end_date: Window end, ``YYYY-MM-DD``. Required whenever ``start_date`` is
                given.
            past_days: Number of **complete past days**, ending yesterday (0-92). It
                adds no forecast days, so a retention question about last week runs on
                last week's observations only.
            forecast_days: Number of days from **today** forward (0-16). Combine it
                with ``past_days`` to simulate across today; with neither given, the
                window is the coming 7 days.
            initial_soil_moisture_pct: Optional day-1 soil moisture, in **% water
                content** (e.g. ``18.5``). **Omit it in normal use** — the tool reads
                the roof's own sensor for the day the window opens. Pass it only for
                a what-if ("if the roof started out dry, at 5 %") or when the user
                states a starting value. The first day only seeds the model, so
                starting the window a few days early also lets the state settle.
            albedo: Optional override of the roof's surface albedo (0.0-1.0), the
                fraction of sunlight reflected. **Omit it in normal use** — each roof
                type has a calibrated default (0.06 for the wetland's open water, 0.2
                for the vegetated roofs). Pass it only when the user explicitly
                describes a different surface or asks a what-if: e.g. ~0.25-0.3 dry
                or sparse vegetation, ~0.4-0.6 a light gravel or reflective "cool
                roof" coating, ~0.8 fresh snow. A higher albedo reflects more energy
                away, which lowers evapotranspiration and leaves the roof wetter. One
                value applies to the whole window. Never set it to make a result
                match an expectation, and when it is set, say so in the answer.

        Returns:
            dict: on success ``status='success'`` with the resolved ``roof_type``,
            the effective ``parameters`` (including the albedo actually used), the
            ``seed`` that day 1 started from (its %θ, where it came from, and whether
            the reading was stale — say so in the answer if it was), a ``data`` list
            (one day per row, with ``swc_pct`` and ``Ssub``), and a ``summary``
            (retention mm/%, driest day, drought flag). When the request is outside
            what can be modelled — the gravel roof, or a window with no soil-moisture
            record to start from — ``status='not_available'`` with a ``reason`` to
            pass on to the user; that is a scope limit, not a malfunction. On failure
            ``status='error'`` with ``error_details``.
        """
        non_modellable = NON_MODELLABLE_ROOFS.get(roof_type.strip().lower())
        if non_modellable is not None:
            logger.info("Green-roof model asked for an unmodellable roof", roof_type=roof_type)
            return NotAvailableResult(reason=non_modellable).model_dump()

        if roof_type not in ROOF_PRESETS:
            valid = ", ".join(sorted(ROOF_PRESETS))
            return ErrorResult(
                error_details=f"Unknown roof_type {roof_type!r}. Valid types: {valid}."
            ).model_dump()

        # Checked before the weather fetch so a bad value fails without a wasted hop.
        if albedo is not None and not 0.0 <= albedo <= 1.0:
            return ErrorResult(
                error_details=f"albedo must be between 0.0 and 1.0, got {albedo}."
            ).model_dump()

        if initial_soil_moisture_pct is not None and not 0.0 <= initial_soil_moisture_pct <= 100.0:
            return ErrorResult(
                error_details=(
                    "initial_soil_moisture_pct is a percentage water content and must be "
                    f"between 0 and 100, got {initial_soil_moisture_pct}."
                )
            ).model_dump()

        # Resolved before the fetch: Open-Meteo's own past_days silently appends a
        # seven-day forecast tail, which would be simulated as if it were observed.
        # The day it resolves against is the context's, read per call.
        today = ctx.clock().date()
        try:
            window_start, window_end = resolve_window(
                start_date, end_date, past_days, forecast_days, today=today
            )
        except InvalidWindowError as exc:
            logger.info("Rejected green-roof window", error=str(exc))
            return ErrorResult(error_details=str(exc)).model_dump()

        try:
            weather = await fetch_daily_weather(
                SITE_LATITUDE,
                SITE_LONGITUDE,
                start_date=window_start,
                end_date=window_end,
                today=today,
            )
        except WeatherFetchError as exc:
            # Upstream said what was wrong; passing it on lets the agent fix the window.
            logger.warning(
                "Weather fetch rejected for green-roof analysis",
                window=(window_start, window_end),
                error=str(exc),
            )
            return ErrorResult(error_details=str(exc)).model_dump()
        except Exception:
            logger.exception("Weather fetch failed for green-roof analysis")
            return ErrorResult(
                error_details=(
                    f"Failed to fetch weather for the roof over {window_start}..{window_end}. "
                    "Try a different date window."
                )
            ).model_dump()

        if not weather.data:
            return ErrorResult(
                error_details="No weather days were returned for that window. Try a different one."
            ).model_dump()

        # The seed describes the roof going into day 1, so it is read as of the day
        # the window actually opens — whatever form the window was expressed in.
        window_start = date.fromisoformat(weather.data[0].Date)
        try:
            seed = await _resolve_seed(
                ctx.db,
                roof_type,
                window_start,
                float(ROOF_PRESETS[roof_type]["SH"]),
                initial_soil_moisture_pct,
            )
        except SwcUnavailableError as exc:
            logger.info("No SWC seed for green-roof model", roof_type=roof_type, error=str(exc))
            return NotAvailableResult(reason=str(exc)).model_dump()
        except Exception:
            logger.exception("Failed to read the roof's soil moisture")
            return ErrorResult(
                error_details="Failed to read the roof's soil moisture from the database."
            ).model_dump()

        # Surveyed site height, not Open-Meteo's ~1 km grid-cell elevation.
        parameters = resolve_roof_parameters(
            roof_type,
            hoehe_nn=SITE_ELEVATION_M,
            lat=SITE_LATITUDE,
            long=SITE_LONGITUDE,
            theta_01=seed.substrate_storage_mm,
            albedo=albedo,
        )

        try:
            result_rows = await run_gr2l(weather.data, parameters)
        except Gr2lConfigError as exc:
            logger.error("GR2L not configured", error=str(exc))
            return ErrorResult(error_details=str(exc)).model_dump()
        except Exception:
            logger.exception("GR2L prediction failed")
            return ErrorResult(
                error_details="The green-roof model service is unavailable. The responsible team is looking into it."
            ).model_dump()

        days = _to_days(result_rows, parameters, roof_type)
        summary = _summarize(weather.data, days, parameters)
        return GreenRoofBalanceResult(
            roof_type=roof_type,
            parameters=parameters,
            seed=seed,
            data=days,
            summary=summary,
        ).model_dump()

    # ADK reads `__name__`; the qualname is reset so a `<locals>`-qualified name
    # never surfaces in logs or reprs (as in ``warehouse.make_query_database_tool``).
    predict_green_roof_water_balance_tool.__qualname__ = (
        "predict_green_roof_water_balance_tool"
    )
    return predict_green_roof_water_balance_tool
