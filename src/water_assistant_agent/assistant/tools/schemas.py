"""Pydantic request/response models for the weather + GR2L green-roof tools.

Mirrors the reference gateway contract (``Gr2lRequest`` / ``Gr2lErgebnis`` in
``weinbau-api-v1/api_gateway/prediction_models/gr2l.py``). The pure clients
(:mod:`weather_client`, :mod:`gr2l_client`) return these models; the ADK tool
wrappers return ``.model_dump()`` dicts so the error path stays a plain
status-dict (consistent with ``warehouse.py``) and ADK/LLM serialization is
unambiguous. Every top-level model carries a ``status`` literal so the agent can
branch on success/error without guessing.
"""

from typing import Literal

from pydantic import BaseModel, Field


class ErrorResult(BaseModel):
    """Uniform failure payload returned by the tool wrappers."""

    status: Literal["error"] = "error"
    error_details: str


class NotAvailableResult(BaseModel):
    """The request is well-formed but outside what this deployment can model.

    Distinct from :class:`ErrorResult` on purpose: nothing has gone wrong, so the
    agent must report a scope limit rather than a system fault. The gravel roof
    is the standing case — it has no substrate, so GR2L has nothing to simulate,
    while its sensors are still queryable like any other roof's.
    """

    status: Literal["not_available"] = "not_available"
    reason: str


class DailyWeatherRow(BaseModel):
    """One GR2L-ready weather day (weather output == GR2L input row).

    Already transposed from Open-Meteo's column-oriented response and
    unit-converted: ``gs`` is J/cm²/day (``shortwave_radiation_sum`` × 100) and
    ``w`` is km/h. See ``weather_tool.md`` for the conversion rationale.
    """

    Date: str
    tm: float = Field(description="Mean temperature, °C")
    tx: float = Field(description="Max temperature, °C")
    tn: float = Field(description="Min temperature, °C")
    rf: float = Field(description="Relative humidity, %")
    precip: float = Field(description="Precipitation, mm")
    w: float = Field(description="Wind speed, km/h")
    gs: float = Field(description="Global radiation, J/cm²/day")


class WeatherPeriod(BaseModel):
    """Daily weather rows aggregated over a span — one week, or a whole window.

    Field names are the daily row's, and each is aggregated the way that field is
    defined: ``tx`` is the span's hottest day, ``tn`` its coldest night,
    ``precip`` and ``gs`` totals, the rest means. See :mod:`series`.
    """

    start: str
    end: str
    days: int
    tm: float | None = Field(description="Mean temperature over the span, °C")
    tx: float = Field(description="Highest daily maximum, °C")
    tn: float = Field(description="Lowest daily minimum, °C")
    rf: float | None = Field(description="Mean relative humidity, %")
    precip: float | None = Field(description="Total precipitation, mm")
    w: float | None = Field(description="Mean wind speed, km/h")
    gs: float | None = Field(description="Total global radiation, J/cm²/day summed")


class RoofPeriod(BaseModel):
    """GR2L days aggregated over a span: fluxes accumulated, states averaged."""

    start: str
    end: str
    days: int
    ET_PM: float | None = Field(description="Potential ET over the span, mm")
    ET: float | None = Field(description="Actual ET over the span, mm")
    Qdown: float | None = Field(description="Percolation sub→ret over the span, mm")
    Qup: float | None = Field(description="Capillary uptake ret→sub over the span, mm")
    OUT: float | None = Field(description="Outflow / runoff over the span, mm")
    Ssub: float | None = Field(description="Mean substrate storage, mm")
    Sret: float | None = Field(description="Mean retention storage, mm")
    swc_pct: float | None = Field(description="Mean substrate water content, %θ")
    min_swc_pct: float | None = Field(description="Driest day in the span, %θ")


class WeatherResult(BaseModel):
    """Daily weather over one absolute window, from whichever source served it.

    Carries **no ``elevation``**: a weather source cannot know the surveyed height
    of a roof, only the height of whatever cell or mast it answered from, and the
    two differ by tens of metres in a city. Elevation is a site fact — the tool
    wrapper composes ``site.py``'s own value into its agent-facing payload, and
    GR2L's ``hoehe_nn`` comes from the same place through its own wrapper
    (``agent_architecture.md`` §3.3).
    """

    status: Literal["success"] = "success"
    latitude: float
    longitude: float
    timezone: str
    source: Literal["station", "archive"] = Field(
        description="Which source served the whole window — the site's own station, "
        "or the Open-Meteo reanalysis. Chosen in code from the window, never named "
        "by the agent; an answer discloses it whenever it is the station"
    )
    data: list[DailyWeatherRow]
    truncated: bool = Field(
        default=False,
        description="The window is longer than the daily-series cap, so `data` is "
        "empty and `summary` plus `weekly` carry the window instead",
    )
    summary: WeatherPeriod | None = Field(
        default=None, description="The whole window aggregated (truncated responses only)"
    )
    weekly: list[WeatherPeriod] | None = Field(
        default=None, description="Consecutive weekly aggregates (truncated responses only)"
    )


class Gr2lRequest(BaseModel):
    """Request body for ``POST {gr2l_api_base_url}/predict_gr2l``.

    Serialize with ``model_dump(exclude_none=True)`` so unset optionals are
    dropped, matching the gateway's own forwarding behaviour.
    """

    data: list[DailyWeatherRow]
    hoehe_nn: float = Field(default=120, description="Elevation above sea level, m")
    lat: float = Field(default=52, description="Latitude, decimal degrees")
    long: float = Field(default=11, description="Longitude, decimal degrees")
    SH: float = Field(default=6, description="Substrate height, cm")
    Ssubmin: float = Field(default=5.4, description="Min substrate water content, mm")
    Ssubmax: float = Field(default=25.4, description="Max substrate water content, mm")
    Sret: float = Field(default=0, description="Initial retention storage, mm")
    Sretmax: float = Field(default=5.0, description="Max retention storage, mm")
    theta_01: float = Field(default=20, description="Initial substrate moisture, mm")
    theta_02: float = Field(default=5, description="Initial retention moisture, mm")
    kg: float = Field(default=0.35, description="Crop / vegetation coefficient")
    albedo: float = Field(default=0.2, description="Surface albedo used in net-radiation")
    open_water: bool = Field(
        default=False,
        description="Open-water surface (e.g. wetland storage mat): ET actual = ET "
        "potential (no Ssub/Ssubmax throttling); the substrate water balance "
        "(Ssub/Sret/Qdown/Qup/OUT) is still tracked",
    )


class Gr2lResultRow(BaseModel):
    """One GR2L output day (mirrors the gateway ``Gr2lErgebnisRow``)."""

    Date: str
    ET_PM: float = Field(description="Potential ET (Penman-Monteith), mm/day")
    Ssub: float | None = Field(default=None, description="Substrate storage, mm")
    Sret: float | None = Field(default=None, description="Retention storage, mm")
    Qdown: float | None = Field(default=None, description="Percolation sub→ret, mm (null day 1)")
    Qup: float | None = Field(default=None, description="Capillary uptake ret→sub, mm (null day 1)")
    OUT: float | None = Field(default=None, description="Outflow / runoff, mm (null day 1)")
    ET: float = Field(description="Actual ET, mm/day")


class GreenRoofDay(Gr2lResultRow):
    """One GR2L output day, with the substrate storage restated as %θ.

    ``swc_pct`` is the primary soil-moisture quantity for the agent (sensors and
    the ops manual both speak %θ); ``Ssub`` stays alongside it for the water
    balance. It is ``None`` for the wetland, whose ponded storage has no θ
    equivalent above its 17 mm mat — see ``swc.MM_ONLY_ROOFS``.
    """

    swc_pct: float | None = Field(
        default=None, description="Substrate volumetric water content, %θ (null for the wetland)"
    )


class SwcSeed(BaseModel):
    """Where day 1's substrate state came from.

    Reported so an answer can disclose a seed the researcher did not choose —
    particularly a stale one, since substrate moisture has a memory of days.
    """

    source: Literal["measured", "caller"]
    swc_pct: float = Field(description="Day-1 volumetric water content, %θ")
    substrate_storage_mm: float = Field(description="The same state in mm, as sent to GR2L")
    measured_at: str | None = Field(
        default=None, description="Timestamp of the sensor reading (measured seeds only)"
    )
    age_days: int | None = Field(
        default=None, description="Days between the reading and the window start"
    )
    is_stale: bool = Field(
        default=False, description="Reading is older than the freshness window; say so in the answer"
    )


class RoofParameters(BaseModel):
    """The parameters actually sent to GR2L (roof preset + geo + moisture seed)."""

    SH: float
    Ssubmin: float
    Ssubmax: float
    Sret: float
    Sretmax: float
    theta_01: float
    theta_02: float
    kg: float
    albedo: float
    open_water: bool
    hoehe_nn: float
    lat: float
    long: float


class GreenRoofSummary(BaseModel):
    """Derived from the GR2L per-day series for quick agent reasoning."""

    days: int
    total_precip_mm: float
    total_outflow_mm: float
    retention_mm: float = Field(description="Σprecip − ΣOUT over the window")
    retention_pct: float | None = Field(
        description="Retained share of the window's rain; null when no rain fell"
    )
    min_substrate_storage_mm: float
    min_swc_pct: float | None = Field(
        default=None, description="Driest day's water content, %θ (null for the wetland)"
    )
    drought_stress: bool = Field(description="Ssub approaches Ssubmin and ET collapses")
    retention_excludes_seed_day_runoff: bool = Field(
        default=False,
        description="Day 1 is a seed day with null OUT, so its rain counts as retained: "
        "retention is over-reported. Set when the first day was wet; re-run with an "
        "earlier start to get a clean number",
    )


class MeasuredComparison(BaseModel):
    """How far the predicted %θ series ran from what the roof's sensor recorded.

    Present only when the caller asked for it. ``days = 0`` is a real outcome
    rather than a failure — a forecast window has no measured counterpart yet —
    and carries ``reason`` instead of statistics, so an answer says why the
    comparison is missing rather than quoting a deviation of zero.
    """

    days: int = Field(description="Days both series cover")
    overlap_start: str | None = Field(default=None, description="First compared day")
    overlap_end: str | None = Field(default=None, description="Last compared day")
    mean_abs_deviation_pct: float | None = Field(
        default=None, description="Mean |predicted − measured| over the overlap, %θ"
    )
    max_abs_deviation_pct: float | None = Field(
        default=None, description="Largest single-day |predicted − measured|, %θ"
    )
    reason: str | None = Field(
        default=None, description="Why there is no overlap to compare (only when days is 0)"
    )


class GreenRoofBalanceResult(BaseModel):
    """Result of :func:`predict_green_roof_water_balance_tool`."""

    status: Literal["success"] = "success"
    roof_type: str
    forcings: dict[str, dict[str, float]] | None = Field(
        default=None,
        description="The counterfactual overrides applied to the fetched weather "
        "before the model ran, echoed exactly as they were applied — "
        "`{field: {day: value}}` in `DailyWeatherRow`'s own field names. Null when "
        "the run was driven by the weather as fetched",
    )
    weather_source: Literal["station", "archive"] = Field(
        description="Which source forced the run — the same field, from the same "
        "choice, as the weather tool's `source`. A retrospective run is forced by "
        "the site's own instruments; an answer says so"
    )
    parameters: RoofParameters
    seed: SwcSeed
    data: list[GreenRoofDay]
    truncated: bool = Field(
        default=False,
        description="The window is longer than the daily-series cap, so `data` is "
        "empty and `weekly` carries the run beside the summary",
    )
    weekly: list[RoofPeriod] | None = Field(
        default=None, description="Consecutive weekly aggregates (truncated responses only)"
    )
    summary: GreenRoofSummary
    evaluation: MeasuredComparison | None = Field(
        default=None,
        description="Deviation from the measured record, present only when the "
        "caller asked for it with `evaluate_against_measured`",
    )
