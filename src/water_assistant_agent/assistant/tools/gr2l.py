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

Counterfactuals are applied to the *forcing*, never to the result: ``forcings``
overlays the fetched rows before the request, so the model computes the what-if
rather than this wrapper adjusting an answer afterwards. The overlay is keyed by
``DailyWeatherRow``'s own field names — one vocabulary, no translation table
(``decisions.md`` § GR2L argument surface).

The tool is produced by :func:`make_green_roof_balance_tool`, which closes over one
:class:`~water_assistant_agent.assistant.context.ScenarioContext`: the window
resolves against ``ctx.as_of``, the weather comes from ``ctx.weather`` and the
soil-moisture seed is read through ``ctx.db``, so a case sees its own as-of views,
its own day and its own forcing, and none is a module-level singleton
(``agent_architecture.md`` §4).
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog
from google.adk.tools.tool_context import ToolContext

from water_assistant_agent.assistant.ports import ReadOnlyWarehouseQuery
from water_assistant_agent.assistant.tools.gr2l_client import (
    NON_MODELLABLE_ROOFS,
    ROOF_PRESETS,
    Gr2lConfigError,
    normalize_roof_type,
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
    MeasuredComparison,
    NotAvailableResult,
    RoofParameters,
    SwcSeed,
)
from water_assistant_agent.assistant.tools.series import (
    MAX_SERIES_DAYS,
    WEEK_DAYS,
    roof_periods,
)
from water_assistant_agent.assistant.tools.site import (
    SITE_ELEVATION_M,
    SITE_LATITUDE,
    SITE_LONGITUDE,
)
from water_assistant_agent.assistant.tools.swc import (
    SwcUnavailableError,
    daily_mean_swc,
    latest_measured_swc,
    mm_to_theta_pct,
    theta_pct_to_mm,
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

GreenRoofBalanceTool = Callable[..., Awaitable[dict[str, Any]]]
"""What :func:`make_green_roof_balance_tool` returns: the ADK-facing GR2L tool."""

# Fraction of the substrate storage range above Ssubmin below which the roof is
# considered water-stressed.
_DROUGHT_FRACTION = 0.05

FORCEABLE_FIELDS: tuple[str, ...] = tuple(
    name for name in DailyWeatherRow.model_fields if name != "Date"
)
"""What a counterfactual may override — ``DailyWeatherRow``'s own field names.

Derived from the row model rather than listed, because the point of the
vocabulary is that there is only one: a forcing argument and the row it replaces
must never need translating, and a hand-kept list is a second place for a field
rename to go wrong silently (``decisions.md`` § GR2L argument surface). ``Date``
is excluded — it is the key a forcing is addressed by, not a value it can move.
"""


class ForcingError(ValueError):
    """A ``forcings`` argument that cannot be applied — an argument fault, pre-I/O.

    Raised before the weather fetch, so a malformed counterfactual costs no
    upstream hop and the message names what to fix.
    """


def _normalize_forcings(
    forcings: dict[str, dict[str, float]],
    window_start: str,
    window_end: str,
) -> dict[str, dict[str, float]]:
    """Validate *forcings* against the resolved window; return it with float values.

    Sparse by construction: nothing here requires a day to be present, only that
    every day named *is* in the window. A field the caller did not mention keeps
    the fetched value for every day, and a day it did not mention keeps it for
    that field.

    Raises:
        ForcingError: a field that is not one of :data:`FORCEABLE_FIELDS`, a day
            that is not ``YYYY-MM-DD`` or falls outside the window, or a value
            that is not a number.
    """
    if not isinstance(forcings, dict):
        raise ForcingError(
            "forcings must map a weather field to a {day: value} mapping, e.g. "
            '{"precip": {"2026-07-22": 50.0}}.'
        )

    start, end = date.fromisoformat(window_start), date.fromisoformat(window_end)
    normalized: dict[str, dict[str, float]] = {}
    for field, days in forcings.items():
        if field not in FORCEABLE_FIELDS:
            valid = ", ".join(FORCEABLE_FIELDS)
            raise ForcingError(
                f"forcings names {field!r}, which is not a weather field. Valid fields: {valid}."
            )
        if not isinstance(days, dict):
            raise ForcingError(
                f"forcings[{field!r}] must map days to values, e.g. "
                f'{{"{start.isoformat()}": 1.0}}.'
            )
        values: dict[str, float] = {}
        for day, value in days.items():
            try:
                parsed = date.fromisoformat(str(day))
            except ValueError:
                raise ForcingError(
                    f"forcings[{field!r}] has key {day!r}; days must be YYYY-MM-DD dates."
                ) from None
            if not start <= parsed <= end:
                raise ForcingError(
                    f"forcings[{field!r}] names {parsed.isoformat()}, which is outside the "
                    f"window {window_start}..{window_end}. Force only days the run covers."
                )
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise ForcingError(
                    f"forcings[{field!r}][{parsed.isoformat()!r}] must be a number, got {value!r}."
                )
            values[parsed.isoformat()] = float(value)
        normalized[field] = values
    return normalized


def _apply_forcings(
    rows: list[DailyWeatherRow],
    forcings: dict[str, dict[str, float]],
) -> tuple[list[DailyWeatherRow], set[str]]:
    """Overlay *forcings* on the fetched *rows*; return them and the days that landed.

    The rows GR2L runs on are the fetched ones with the named fields replaced —
    the counterfactual is applied to the *forcing*, not to the result, so the
    model computes it rather than the wrapper adjusting an answer afterwards.
    The returned day set is what the caller checks the request against: a day the
    fetch did not return is a day the override silently would not have reached.
    """
    forced_days = {day for days in forcings.values() for day in days}
    if not forced_days:
        return rows, set()

    applied: set[str] = set()
    overlaid: list[DailyWeatherRow] = []
    for row in rows:
        updates = {
            field: days[row.Date] for field, days in forcings.items() if row.Date in days
        }
        if updates:
            applied.add(row.Date)
            overlaid.append(row.model_copy(update=updates))
        else:
            overlaid.append(row)
    return overlaid, applied


def _to_days(
    result_rows: list[Gr2lResultRow],
    parameters: RoofParameters,
) -> list[GreenRoofDay]:
    """Restate each GR2L day's mm storage as %θ, keeping the mm alongside.

    One route, no roof-dependent branch: every roof this layer serves is a
    substrate roof whose storage converts, and the one whose storage did not —
    the wetland — is declined at entry rather than answered in another unit
    (``gr2l_client.NON_MODELLABLE_ROOFS``). ``swc_pct`` stays optional only for
    the null ``Ssub`` an older model build could return.
    """
    return [
        GreenRoofDay(
            **row.model_dump(),
            swc_pct=(
                None
                if row.Ssub is None
                else round(mm_to_theta_pct(row.Ssub, parameters.SH), 2)
            ),
        )
        for row in result_rows
    ]


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


def _compare_to_measured(
    days: list[GreenRoofDay],
    measured: dict[str, float],
) -> MeasuredComparison:
    """Deviation statistics over the days both the prediction and the record hold.

    The join is on the day itself, so the overlap is whatever the two series
    share — a forecast tail simply has no counterpart, and a gap in the record
    drops that day rather than the window. Deviations are absolute and in %θ, the
    unit both sides already speak, so nothing is converted to compare them.
    """
    overlap = [
        (row.Date, abs(row.swc_pct - measured[row.Date]))
        for row in days
        if row.swc_pct is not None and row.Date in measured
    ]
    if not overlap:
        return MeasuredComparison(
            days=0,
            reason=(
                "The soil-moisture record covers none of the simulated days, so there is "
                "nothing to compare this run against."
            ),
        )
    deviations = [deviation for _, deviation in overlap]
    return MeasuredComparison(
        days=len(overlap),
        overlap_start=overlap[0][0],
        overlap_end=overlap[-1][0],
        mean_abs_deviation_pct=round(sum(deviations) / len(deviations), 2),
        max_abs_deviation_pct=round(max(deviations), 2),
    )


async def _resolve_seed(
    executor: ReadOnlyWarehouseQuery,
    roof_type: str,
    window_start: date,
    sh_cm: float,
    caller_swc_pct: float | None,
    *,
    as_of: datetime,
) -> SwcSeed:
    """Establish day 1's substrate state, in %θ and in the mm GR2L consumes.

    The caller's value wins when given; otherwise the roof's own sensor supplies
    it, read through *executor* at ``min(window_start, as_of)`` — the one seed
    rule (``swc.seed_bound``), never a bound this wrapper invents. Propagates
    :class:`SwcUnavailableError` when neither exists — the tool never falls back
    to a made-up state.
    """
    if caller_swc_pct is not None:
        return SwcSeed(
            source="caller",
            swc_pct=caller_swc_pct,
            substrate_storage_mm=round(theta_pct_to_mm(caller_swc_pct, sh_cm), 3),
        )

    measured = await asyncio.to_thread(
        latest_measured_swc, executor, roof_type, window_start, as_of=as_of
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

    Three bindings, then: the clock, the executor, and ``ctx.weather``. The last
    is what makes the model's forcing the *same* rows the standalone weather tool
    would report for the same window, from the same source — a retrospective run
    is forced by the very instruments its measured comparison came from
    (``decisions.md`` § Weather sources).
    """

    async def predict_green_roof_water_balance_tool(
        roof_type: str,
        start_date: str | None = None,
        end_date: str | None = None,
        past_days: int | None = None,
        forecast_days: int | None = None,
        initial_soil_moisture_pct: float | None = None,
        albedo: float | None = None,
        forcings: dict[str, dict[str, float]] | None = None,
        evaluate_against_measured: bool = False,
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
        appear alongside for the water balance.

        Three roofs can be modelled: ``non_irrigated_extensive``,
        ``irrigated_extensive``, ``semi_intensive``. The **gravel roof and the
        wetland cannot be** — the gravel roof has no substrate to simulate, and the
        wetland ponds water above a mat its sensor cannot measure through — and the
        tool reports either as ``status='not_available'``. Their measured sensor
        data is still available through the database, so a question about what
        those two roofs *did* is a normal database question.

        All roofs are segments of the same building, so no location is needed. Past
        and future windows are both supported and resolved automatically; just name
        the dates the question is about (up to 16 days ahead).

        Args:
            roof_type: One of ``non_irrigated_extensive``, ``irrigated_extensive``,
                ``semi_intensive`` — selects the roof's physical parameters. Name
                the roof the user actually asked about even when it is the gravel
                roof or the wetland: the tool answers that it cannot model those,
                which is the honest answer to give.
            start_date: Window start, ``YYYY-MM-DD`` (give ``end_date`` with it).
            end_date: Window end, ``YYYY-MM-DD``. Required whenever ``start_date`` is
                given.
            past_days: Number of **complete past days**, ending yesterday. It adds no
                forecast days, so a retention question about last week runs on last
                week's observations only.
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
                type has a calibrated default (0.2 for these vegetated roofs). Pass
                it only when the user explicitly
                describes a different surface or asks a what-if: e.g. ~0.25-0.3 dry
                or sparse vegetation, ~0.4-0.6 a light gravel or reflective "cool
                roof" coating, ~0.8 fresh snow. A higher albedo reflects more energy
                away, which lowers evapotranspiration and leaves the roof wetter. One
                value applies to the whole window. Never set it to make a result
                match an expectation, and when it is set, say so in the answer.
            forcings: Optional **what-if weather**, replacing the fetched value for
                the days named and leaving every other day and field as measured or
                forecast: ``{"precip": {"2026-07-22": 50.0}}`` runs the window with
                50 mm of rain on 22 July. Use it when the user asks what *would*
                happen under different weather ("what if we got a 50 mm downpour
                tomorrow?", "what if next week were 5 °C warmer?"); omit it
                otherwise, and never use it to nudge a result toward an expectation.
                The fields are the same ones the day rows carry — ``precip`` (mm),
                ``tm`` / ``tx`` / ``tn`` (°C), ``rf`` (%), ``w`` (km/h), ``gs``
                (J/cm²/day) — and every day named must fall inside the window.
                Say in the answer which values were assumed rather than measured.
            evaluate_against_measured: Set it to ``True`` when the user asks how
                well the model matches reality — "how close was the simulation to
                the sensor?", "did the model get last month right?" — and the
                result gains an ``evaluation``: the mean and largest daily gap
                between predicted and measured soil moisture, in the same %θ, over
                the days both cover. Only past windows have anything to compare
                against; on a forecast the comparison comes back with ``days: 0``
                and a reason, which is not a failure. Leave it off otherwise: it
                costs an extra database read and answers a question about the
                model rather than about the roof.

        Returns:
            dict: on success ``status='success'`` with the resolved ``roof_type``,
            the ``weather_source`` the run was forced by (``'station'`` means the
            site's own instruments — say so in the answer), the ``forcings``
            actually applied (null when none were, and otherwise the what-if
            values the answer must attribute), the effective ``parameters``
            (including the albedo actually used), the
            ``seed`` that day 1 started from (its %θ, where it came from, and whether
            the reading was stale — say so in the answer if it was), a ``data`` list
            (one day per row, with ``swc_pct`` and ``Ssub``), and a ``summary``
            (retention mm/%, driest day, drought flag). A window longer than 31
            days comes back with ``truncated: true`` and an empty ``data``,
            replaced by ``weekly`` aggregates — the summary still covers the whole
            run, so answer from those rather than re-running the window in pieces.
            With ``evaluate_against_measured`` it also carries ``evaluation``: the
            compared days, the overlap window, and the mean and largest
            |predicted − measured| soil moisture in %θ. When the request is outside
            what can be modelled — the gravel roof or the wetland, a window with no
            soil-moisture record to start from, or a window ending more than 16
            days ahead, since no weather exists to drive the model that far out —
            ``status='not_available'`` with a ``reason`` to
            pass on to the user; that is a scope limit, not a malfunction. On failure
            ``status='error'`` with ``error_details`` and an ``error_type``:
            ``'invalid_argument'`` means the call itself was wrong and can be
            corrected and retried, ``'upstream'`` means something the tool depends
            on failed — report the system-side problem rather than retrying.
        """
        # Normalized once, here, and every lookup below uses the result — the
        # scope table, the presets, the soil-moisture column and the echoed
        # `roof_type` alike (`gr2l_client.normalize_roof_type`).
        roof_type = normalize_roof_type(roof_type)

        non_modellable = NON_MODELLABLE_ROOFS.get(roof_type)
        if non_modellable is not None:
            logger.info("Green-roof model asked for an unmodellable roof", roof_type=roof_type)
            return NotAvailableResult(reason=non_modellable).model_dump()

        if roof_type not in ROOF_PRESETS:
            valid = ", ".join(sorted(ROOF_PRESETS))
            return ErrorResult(
                error_type="invalid_argument",
                error_details=f"Unknown roof_type {roof_type!r}. Valid types: {valid}.",
            ).model_dump()

        # Checked before the weather fetch so a bad value fails without a wasted hop.
        if albedo is not None and not 0.0 <= albedo <= 1.0:
            return ErrorResult(
                error_type="invalid_argument",
                error_details=f"albedo must be between 0.0 and 1.0, got {albedo}.",
            ).model_dump()

        if initial_soil_moisture_pct is not None and not 0.0 <= initial_soil_moisture_pct <= 100.0:
            return ErrorResult(
                error_type="invalid_argument",
                error_details=(
                    "initial_soil_moisture_pct is a percentage water content and must be "
                    f"between 0 and 100, got {initial_soil_moisture_pct}."
                ),
            ).model_dump()

        # Resolved before the fetch: Open-Meteo's own past_days silently appends a
        # seven-day forecast tail, which would be simulated as if it were observed.
        # The day it resolves against is `ctx.as_of`'s, read per call.
        today = ctx.as_of.date()
        try:
            window_start, window_end = resolve_window(
                start_date, end_date, past_days, forecast_days, today=today
            )
        except InvalidWindowError as exc:
            logger.info("Rejected green-roof window", error=str(exc))
            return ErrorResult(
                error_type="invalid_argument", error_details=str(exc)
            ).model_dump()

        # The same scope limit the weather tool signals, on the same resolved
        # window and against the same `ctx.as_of` (`weather_client.beyond_horizon`).
        # A model run needs a forcing for every day it simulates, and nothing
        # upstream refuses a future window — Archive answers day 400 without
        # complaint — so without this a simulation of unobtainable weather would
        # come back looking like an ordinary answer.
        if beyond_horizon(window_end, today):
            logger.info(
                "Green-roof window reaches past the forecast horizon",
                window=(window_start, window_end),
                as_of=today.isoformat(),
            )
            return NotAvailableResult(
                reason=(
                    f"The roof can only be simulated up to {FORECAST_HORIZON_DAYS} days ahead "
                    f"(through {today + timedelta(days=FORECAST_HORIZON_DAYS):%Y-%m-%d}), because "
                    f"no weather exists beyond that, and the window asked for ends {window_end}."
                )
            ).model_dump()

        # Checked against the *resolved* window, and still before the fetch: both
        # window forms are bound by the one rule, and a counterfactual naming a day
        # the run does not cover fails without an upstream hop.
        applied_forcings: dict[str, dict[str, float]] = {}
        if forcings:
            try:
                applied_forcings = _normalize_forcings(forcings, window_start, window_end)
            except ForcingError as exc:
                logger.info("Rejected green-roof forcings", error=str(exc))
                return ErrorResult(
                    error_type="invalid_argument", error_details=str(exc)
                ).model_dump()

        try:
            weather = await ctx.weather.fetch(start_date=window_start, end_date=window_end)
        except WeatherFetchError as exc:
            # Upstream said what was wrong; passing it on lets the agent fix the window.
            logger.warning(
                "Weather fetch rejected for green-roof analysis",
                window=(window_start, window_end),
                error=str(exc),
            )
            return ErrorResult(error_type="upstream", error_details=str(exc)).model_dump()
        except Exception:
            logger.exception("Weather fetch failed for green-roof analysis")
            return ErrorResult(
                error_type="upstream",
                error_details=(
                    f"Failed to fetch weather for the roof over {window_start}..{window_end}. "
                    "Try a different date window."
                ),
            ).model_dump()

        if not weather.data:
            return ErrorResult(
                error_type="upstream",
                error_details="No weather days were returned for that window. Try a different one.",
            ).model_dump()

        # The rows the model runs on, counterfactual included. A forced day the
        # fetch did not return would be an override that silently did nothing, so
        # it is reported rather than dropped.
        rows, applied_days = _apply_forcings(weather.data, applied_forcings)
        requested_days = {day for days in applied_forcings.values() for day in days}
        if requested_days - applied_days:
            missing = ", ".join(sorted(requested_days - applied_days))
            return ErrorResult(
                error_type="upstream",
                error_details=(
                    f"The weather source returned no rows for {missing}, so the forcings for "
                    f"{'those days' if len(requested_days - applied_days) > 1 else 'that day'} "
                    "could not be applied."
                ),
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
                as_of=ctx.as_of,
            )
        except SwcUnavailableError as exc:
            logger.info("No SWC seed for green-roof model", roof_type=roof_type, error=str(exc))
            return NotAvailableResult(reason=str(exc)).model_dump()
        except Exception:
            logger.exception("Failed to read the roof's soil moisture")
            return ErrorResult(
                error_type="upstream",
                error_details="Failed to read the roof's soil moisture from the database.",
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
            # `ctx.cache` and nothing else decides whether this hop is cached:
            # a case in replay is served from its committed entry and issues no
            # HTTP at all, while production's `cache=None` calls GR2L live every
            # time (`decisions.md` § The response cache). A hit skips the canary
            # too — there is no service to have moved when nothing is asked.
            result_rows = await run_gr2l(rows, parameters, cache=ctx.cache)
        except Gr2lConfigError as exc:
            logger.error("GR2L not configured", error=str(exc))
            return ErrorResult(error_type="upstream", error_details=str(exc)).model_dump()
        except Exception:
            logger.exception("GR2L prediction failed")
            return ErrorResult(
                error_type="upstream",
                error_details="The green-roof model service is unavailable. The responsible team is looking into it.",
            ).model_dump()

        days = _to_days(result_rows, parameters)
        # Summarized over the rows the model actually ran on: a counterfactual's
        # retention is against its own rain, not against the rain it replaced.
        summary = _summarize(rows, days, parameters)

        evaluation: MeasuredComparison | None = None
        if evaluate_against_measured:
            try:
                # Read through `ctx.db`, so the comparison series is bounded at the
                # same cut as the seed: a case can never be scored against sensor
                # readings taken after its own `as_of`.
                measured = await asyncio.to_thread(
                    daily_mean_swc,
                    ctx.db,
                    roof_type,
                    date.fromisoformat(days[0].Date),
                    date.fromisoformat(days[-1].Date),
                )
            except Exception:
                logger.exception("Failed to read the measured soil-moisture series")
                return ErrorResult(
                    error_type="upstream",
                    error_details=(
                        "Failed to read the roof's measured soil moisture for the comparison."
                    ),
                ).model_dump()
            evaluation = _compare_to_measured(days, measured)

        # The cap is on the response, never on the run: GR2L simulated every day
        # of the window, and the summary and the comparison above are derived from
        # the full series. Only what the model reads back is bounded.
        truncated = len(days) > MAX_SERIES_DAYS
        weekly = roof_periods(days, WEEK_DAYS) if truncated else None
        if truncated:
            logger.info(
                "Truncating the daily green-roof series",
                roof_type=roof_type,
                days=len(days),
            )
            days = []

        return GreenRoofBalanceResult(
            roof_type=roof_type,
            forcings=applied_forcings or None,
            weather_source=weather.source,
            parameters=parameters,
            seed=seed,
            data=days,
            truncated=truncated,
            weekly=weekly,
            summary=summary,
            evaluation=evaluation,
        ).model_dump()

    # ADK reads `__name__`; the qualname is reset so a `<locals>`-qualified name
    # never surfaces in logs or reprs (as in ``warehouse.make_query_database_tool``).
    predict_green_roof_water_balance_tool.__qualname__ = (
        "predict_green_roof_water_balance_tool"
    )
    return predict_green_roof_water_balance_tool
