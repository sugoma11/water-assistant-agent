"""ADK tool: should this roof be irrigated?

The agent-facing wrapper over :mod:`..irrigation`, which is the deployed
controller's own bucket model and priority ladder (``irrigation_tool.md``). This
layer owns three things and no physics: resolving what the rule needs — a seed,
a forcing, an ET0 — deciding which of the two entry points a call takes, and
restating the answer in the units the site speaks.

**Everything runs local, which is what makes irrigation cases fully offline**
(``agent_architecture.md`` §3.5). There is no service behind this tool: no
response-cache entry, no canary, and no ``upstream`` failure that is not the
weather fetch or the database. The oracles import the very functions it calls.

**Two entry points, one ladder.** By default the tool is self-contained on §3.4's
terms — the seed is the roof's own sensor under the ``min(window_start, as_of)``
rule and the forcing is ``ctx.weather`` — and supplying soil moisture, the
maximum temperature and the forecast rain instead makes the call **pure**: no
database, no weather, no simulation. The comparison chain is the same one either
way (:func:`..irrigation.irrigation_decision`).

**ET0 is computed, not fetched.** ``DailyWeatherRow`` carries no ``et0`` and its
schema is frozen, so :mod:`..et_fao56` computes FAO-56 Penman-Monteith locally at
the reference crop's albedo — roof-specific throttling is the stress
coefficient's job, not the forcing's.

**The scope is `NON_MODELLABLE_ROOFS`, shared with GR2L**, and the wording is
this tool's own: the gravel roof and the wetland are declined for reasons that
belong to *this* rule, which reads a soil store in %θ against a wilting point.

The tool is produced by :func:`make_irrigation_tool`, which closes over one
:class:`~..context.ScenarioContext`: the window resolves against ``ctx.as_of``,
the forcing comes from ``ctx.weather`` and the seed is read through ``ctx.db``,
so a case sees its own as-of views and none of it is a module-level singleton
(``agent_architecture.md`` §4).
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

import structlog
from google.adk.tools.tool_context import ToolContext

from water_assistant_agent.assistant.et_fao56 import et0_for_row
from water_assistant_agent.assistant.irrigation import (
    Regime,
    features_from_stated_values,
    irrigation_decision,
    run_roof,
)
from water_assistant_agent.assistant.ports import ReadOnlyWarehouseQuery
from water_assistant_agent.assistant.rules_constants import (
    DECISION_HORIZON_HOURS,
    REFILL_HORIZON_HOURS,
    ROOF_RULES,
    horizon_rows,
    rules_for,
)
from water_assistant_agent.assistant.tools.gr2l_client import (
    NON_MODELLABLE_ROOFS,
    normalize_roof_type,
)
from water_assistant_agent.assistant.tools.roofs import resolve_roof
from water_assistant_agent.assistant.tools.schemas import (
    ErrorResult,
    IrrigationDose,
    IrrigationFeatures,
    IrrigationResult,
    NotAvailableResult,
    SwcSeed,
)
from water_assistant_agent.assistant.tools.site import (
    SITE_ELEVATION_M,
    SITE_LATITUDE,
)
from water_assistant_agent.assistant.tools.swc import (
    SwcUnavailableError,
    latest_measured_swc,
    theta_pct_to_mm,
)
from water_assistant_agent.assistant.tools.weather_client import WeatherFetchError

if TYPE_CHECKING:
    from datetime import datetime

    from water_assistant_agent.assistant.context import ScenarioContext

logger = structlog.get_logger(__name__)

IrrigationTool = Callable[..., Awaitable[dict[str, Any]]]
"""What :func:`make_irrigation_tool` returns: the ADK-facing decision tool."""

DAY_HOURS = 24.0
"""This system's step. The site runs the same rule hourly over its ICON forcing."""

# Which roofs the rule declines, and why *this* rule declines them. Membership is
# `NON_MODELLABLE_ROOFS` — one scope for both water-balance tools
# (`agent_architecture.md` §3.4) — and the wording is the irrigation rule's own:
# GR2L declines the wetland because its ponded storage has no water-content
# contract, and this rule declines it because it has no soil store to read a
# wilting point against. Same roofs, different sentence.
_GRAVEL_REASON = (
    "The gravel roof is not irrigated and has no substrate to hold water, so the "
    "irrigation rule — which decides from a soil store read against a wilting point — "
    "does not apply to it. Its measured sensor data can still be queried."
)

_WETLAND_REASON = (
    "The wetland roof is outside the irrigation rule's scope: the rule is built for a "
    "classical substrate roof, a soil store read in % water content against wilting "
    "and dry thresholds, and the wetland is a ponded fleece mat. The site's own "
    "controller decides it from a lysimeter weight instead, which this deployment does "
    "not model. Its measured sensor data can still be queried."
)

_REASONS: dict[str, str] = {"gravel": _GRAVEL_REASON, "wetland": _WETLAND_REASON}

STATED_ARGUMENTS = ("soil_moisture_pct", "max_temperature_c", "forecast_precip_mm")
"""The three that make a call pure — all of them, or none.

A partly stated call would be part measured and part assumed with no way for the
answer to say which, and the two paths differ in more than provenance: the
modelled one spends part of the forecast rain on evaporation before the roof
fills, and the stated one takes the rain at face value.
"""


def _not_available(roof_type: str) -> dict[str, Any] | None:
    """The scope answer for a roof the rule declines, or ``None`` if it applies.

    Membership comes from the shared table and the roof's identity from
    :mod:`roofs`, so the two spellings of "which roof is this" that already exist
    are the two this reads — there is no third list here.
    """
    if roof_type not in NON_MODELLABLE_ROOFS:
        return None
    roof = resolve_roof(roof_type)
    return NotAvailableResult(reason=_REASONS[roof.name if roof else "gravel"]).model_dump()


async def _measured_seed(
    executor: ReadOnlyWarehouseQuery,
    roof_type: str,
    window_start: date,
    *,
    as_of: "datetime",
) -> SwcSeed:
    """Day 1's soil moisture, from the roof's own sensor.

    The one seed rule, never a bound this wrapper invents
    (:func:`~.swc.seed_bound`), and no caller override: a call that states its own
    soil moisture does not reach here at all — it takes the stated path, which
    fetches nothing.
    """
    rules = rules_for(roof_type)
    measured = await asyncio.to_thread(
        latest_measured_swc, executor, roof_type, window_start, as_of=as_of
    )
    return SwcSeed(
        source="measured",
        swc_pct=round(measured.theta_pct, 2),
        substrate_storage_mm=round(
            theta_pct_to_mm(measured.theta_pct, rules.substrate_height_cm), 3
        ),
        measured_at=measured.measured_at.isoformat(sep=" "),
        age_days=measured.age_days,
        is_stale=measured.is_stale,
    )


def make_irrigation_tool(ctx: "ScenarioContext") -> IrrigationTool:
    """Build ``calc_irrigation`` bound to *ctx*.

    Three bindings, each read per call: ``ctx.as_of`` for the day the window
    opens, ``ctx.weather`` for the forcing and ``ctx.db`` for the seed. Name,
    signature and docstring are the tool declaration ADK sends, so they stay
    identical to the production tool's; :func:`..toolset.build_toolset` is the one
    place ``__doc__`` is replaced, by a candidate.
    """

    async def calc_irrigation(
        roof_type: str,
        soil_moisture_pct: float | None = None,
        max_temperature_c: float | None = None,
        forecast_precip_mm: float | None = None,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Decide whether a green roof should be irrigated, using the site's own rule.

        **Fetches its own weather forecast and reads the roof's own soil-moisture
        sensor** — do not call the weather tool first and do not query the database
        for the roof's moisture. It then runs the deployed controller's water
        balance over the coming week and applies the site's priority ladder.

        The answer is a **decision, not an amount**: ``irrigate`` true or false,
        the rung that decided it, and the fixed dose the site applies when the
        answer is yes. There is no per-case volume to compute — the dose is site
        policy — so never derive one.

        Use it for "should we water the roof?", "does it need irrigating this
        week?", "why is the controller irrigating?" and the like. For how much
        water a roof *retained*, or how its moisture will run over a past window,
        use the water-balance model tool instead: that is a different model, and
        an answer that mixes the two is wrong.

        Three roofs are irrigable: ``irrigated_extensive``,
        ``non_irrigated_extensive`` (advisory — it has no valve), ``semi_intensive``.
        The **gravel roof and the wetland are out of the rule's scope** and come
        back as ``status='not_available'``; name the roof the user asked about
        anyway, because that answer is the honest one.

        The rungs, in the order they are tested, are what ``reason`` reports:

        - ``below_wilting_point`` — the roof dries past its wilting point within
          48 h. Irrigate whatever else is true.
        - ``no_heat_no_stress`` — nothing near 24 °C is forecast, so no cooling is
          wanted and the roof is not critical.
        - ``sufficient_moisture`` — cooling would be welcome, but the substrate is
          wetter than the dry threshold.
        - ``refill_forecast`` — the roof is dry and heat is coming, but rain is
          expected to refill it within the week.
        - ``cooling_requested`` — heat, a dry roof and no rain to wait for.
        - ``no_forecast`` / ``missing_values`` — the window could not be decided
          from; neither irrigates, and a gap is not a dry roof.

        Args:
            roof_type: One of ``irrigated_extensive``, ``non_irrigated_extensive``,
                ``semi_intensive``. Name the roof the user actually asked about
                even when it is the gravel roof or the wetland.
            soil_moisture_pct: Optional **stated** soil moisture, in % water
                content (e.g. ``12.5``). **Omit it in normal use** — the tool reads
                the roof's own sensor. Pass it only for a what-if ("if the roof
                were at 8 %, would you water it?") or when the user states the
                value, and then pass all three stated arguments together: they
                replace the whole measurement, and the tool runs the rule on them
                alone.
            max_temperature_c: Optional **stated** highest temperature expected
                over the next 48 h, °C. Only with the other two.
            forecast_precip_mm: Optional **stated** rain expected over the coming
                week, mm. Only with the other two.

        Returns:
            dict: on success ``status='success'`` with ``irrigate``, the ``reason``
            code, ``inputs`` (``'modelled'`` from the roof's own data, or
            ``'stated'`` from the caller's values — say which in the answer), the
            ``features`` the rung turned on (driest %θ and hottest day of the
            48 h window, whether rain will refill the roof and when), the ``dose``
            to state if the answer is yes (``valve_minutes`` is what the site
            currently applies; ``dose_mm`` is null until the site states it — do
            not invent one), the ``seed`` the run started from (say so in the
            answer when ``is_stale``), and the ``weather_source`` that forced it
            (``'station'`` means the site's own instruments — say so). When the
            rule does not apply — the gravel roof, the wetland, or no
            soil-moisture reading to start from — ``status='not_available'`` with
            a ``reason`` to pass on; that is a scope limit, not a malfunction. On
            failure ``status='error'`` with ``error_details`` and an
            ``error_type``: ``'invalid_argument'`` means the call itself was wrong
            and can be corrected and retried, ``'upstream'`` means something the
            tool depends on failed.
        """
        # Normalized once, here, and every lookup below uses the result — the
        # scope table, the rules and the echoed `roof_type` alike.
        roof_type = normalize_roof_type(roof_type)

        declined = _not_available(roof_type)
        if declined is not None:
            logger.info("Irrigation asked for a roof the rule declines", roof_type=roof_type)
            return declined

        if roof_type not in ROOF_RULES:
            valid = ", ".join(sorted(ROOF_RULES))
            return ErrorResult(
                error_type="invalid_argument",
                error_details=f"Unknown roof_type {roof_type!r}. Valid types: {valid}.",
            ).model_dump()

        if soil_moisture_pct is not None and not 0.0 <= soil_moisture_pct <= 100.0:
            return ErrorResult(
                error_type="invalid_argument",
                error_details=(
                    "soil_moisture_pct is a percentage water content and must be between "
                    f"0 and 100, got {soil_moisture_pct}."
                ),
            ).model_dump()

        rules = rules_for(roof_type)
        stated = {
            "soil_moisture_pct": soil_moisture_pct,
            "max_temperature_c": max_temperature_c,
            "forecast_precip_mm": forecast_precip_mm,
        }

        # --- The stated path: the rule alone, and no I/O of any kind -----------
        if (
            soil_moisture_pct is not None
            and max_temperature_c is not None
            and forecast_precip_mm is not None
        ):
            features = features_from_stated_values(
                rules,
                soil_moisture_pct=soil_moisture_pct,
                max_temperature_c=max_temperature_c,
                forecast_precip_mm=forecast_precip_mm,
            )
            decision = irrigation_decision(features, rules)
            logger.info(
                "Irrigation decided from stated values",
                roof_type=roof_type,
                irrigate=decision.irrigate,
                reason=decision.reason.value,
            )
            return IrrigationResult(
                roof_type=roof_type,
                irrigate=decision.irrigate,
                reason=decision.reason.value,
                inputs="stated",
                features=IrrigationFeatures(
                    min_swc_pct=round(soil_moisture_pct, 2),
                    max_temperature_c=max_temperature_c,
                    will_reach_capacity=features.will_reach_capacity,
                    decision_horizon_hours=DECISION_HORIZON_HOURS,
                    refill_horizon_hours=REFILL_HORIZON_HOURS,
                ),
                dose=IrrigationDose(dose_mm=rules.dose_mm, valve_minutes=rules.valve_minutes),
            ).model_dump()

        given = [name for name, value in stated.items() if value is not None]
        if given:
            missing = ", ".join(name for name in STATED_ARGUMENTS if name not in given)
            return ErrorResult(
                error_type="invalid_argument",
                error_details=(
                    f"Stating {', '.join(given)} without {missing} would decide from a "
                    "half-measured, half-assumed picture. Pass all three of "
                    f"{', '.join(STATED_ARGUMENTS)} to run the rule on stated values, or "
                    "none of them to let the tool read the roof and the forecast itself."
                ),
            ).model_dump()

        # --- The modelled path -------------------------------------------------
        # The window is the refill horizon from today: the decision is about now,
        # so it takes no date arguments, and the rule looks a week ahead for rain.
        today = ctx.as_of.date()
        window_start = today
        window_end = today + timedelta(
            days=horizon_rows(REFILL_HORIZON_HOURS, step_hours=DAY_HOURS) - 1
        )

        try:
            weather = await ctx.weather.fetch(
                start_date=window_start.isoformat(), end_date=window_end.isoformat()
            )
        except WeatherFetchError as exc:
            logger.warning("Weather fetch rejected for the irrigation decision", error=str(exc))
            return ErrorResult(error_type="upstream", error_details=str(exc)).model_dump()
        except Exception:
            logger.exception("Weather fetch failed for the irrigation decision")
            return ErrorResult(
                error_type="upstream",
                error_details=(
                    "Failed to fetch the forecast the irrigation rule needs for "
                    f"{window_start}..{window_end}."
                ),
            ).model_dump()

        if not weather.data:
            return ErrorResult(
                error_type="upstream",
                error_details=(
                    "No forecast days were returned for the coming week, so the roof's "
                    "moisture cannot be projected."
                ),
            ).model_dump()

        try:
            seed = await _measured_seed(ctx.db, roof_type, window_start, as_of=ctx.as_of)
        except SwcUnavailableError as exc:
            logger.info("No SWC seed for the irrigation rule", roof_type=roof_type)
            return NotAvailableResult(reason=str(exc)).model_dump()
        except Exception:
            logger.exception("Failed to read the roof's soil moisture")
            return ErrorResult(
                error_type="upstream",
                error_details="Failed to read the roof's soil moisture from the database.",
            ).model_dump()

        # Surveyed site height and the reference crop's albedo: this is ET0, the
        # forcing, and the roof's own surface belongs to the stress coefficient.
        et0 = [
            et0_for_row(row, latitude=SITE_LATITUDE, elevation_m=SITE_ELEVATION_M)
            for row in weather.data
        ]
        # `tx` is a measured daily maximum where the site's hourly series is a mean
        # per hour — the more faithful quantity at this step, and a stated
        # deviation (`irrigation_tool.md` § Horizons and step).
        run = run_roof(
            roof_type,
            precipitation_mm=[row.precip for row in weather.data],
            et0_mm=et0,
            temperature_c=[row.tx for row in weather.data],
            seed_theta_pct=seed.swc_pct,
            step_hours=DAY_HOURS,
        )
        features, decision = run.features, run.decision

        refill_on = (
            weather.data[features.refill_step].Date
            if features.refill_step is not None and features.refill_step < len(weather.data)
            else None
        )
        logger.info(
            "Irrigation decided from the roof's own data",
            roof_type=roof_type,
            irrigate=decision.irrigate,
            reason=decision.reason.value,
            weather_source=weather.source,
        )
        return IrrigationResult(
            roof_type=roof_type,
            irrigate=decision.irrigate,
            reason=decision.reason.value,
            inputs="modelled",
            features=IrrigationFeatures(
                # Millimetres are internal; the surface speaks the sensors' own unit.
                min_swc_pct=round(
                    Regime.MILLIMETRES.theta_pct_from_store(features.min_store, rules), 2
                ),
                max_temperature_c=features.max_temperature_c,
                will_reach_capacity=features.will_reach_capacity,
                refill_expected_on=refill_on,
                decision_horizon_hours=DECISION_HORIZON_HOURS,
                refill_horizon_hours=REFILL_HORIZON_HOURS,
            ),
            dose=IrrigationDose(dose_mm=rules.dose_mm, valve_minutes=rules.valve_minutes),
            seed=seed,
            weather_source=weather.source,
            window_start=weather.data[0].Date,
            window_end=weather.data[-1].Date,
        ).model_dump()

    # ADK reads `__name__`; the qualname is reset so a `<locals>`-qualified name
    # never surfaces in logs or reprs (as in ``warehouse.make_query_database_tool``).
    calc_irrigation.__qualname__ = "calc_irrigation"
    return calc_irrigation
