"""Pydantic request/response models for the weather + GR2L green-roof tools.

Mirrors the reference gateway contract (``Gr2lRequest`` / ``Gr2lErgebnis`` in
``weinbau-api-v1/api_gateway/prediction_models/gr2l.py``). The pure clients
(:mod:`weather_client`, :mod:`gr2l_client`) return these models; the ADK tool
wrappers return ``.model_dump()`` dicts so the error path stays a plain
status-dict (consistent with ``warehouse.py``) and ADK/LLM serialization is
unambiguous. Every top-level model carries a ``status`` literal so the agent can
branch on success/error without guessing.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from water_assistant_agent.assistant.knowledge.store import Provenance


ErrorType = Literal["invalid_argument", "upstream"]
"""Which of the two populations a failure belongs to (``agent_architecture.md`` §3).

``invalid_argument`` is deterministic argument validation performed **before any
I/O**, echoing what would have been valid; ``upstream`` covers HTTP, database and
configuration failures, including a cache miss the service cannot fill and a
diverged canary. The split is not cosmetic: §7 excludes an ``upstream`` failure
from every aggregate as a harness error the candidate could not have avoided,
while an ``invalid_argument`` is scored — excluding those too would let a
candidate raise its own average by failing on the templates it handles worst
(``decisions.md`` § Tool errors and harness exclusion).
"""


class ErrorResult(BaseModel):
    """Uniform failure payload returned by the tool wrappers."""

    status: Literal["error"] = "error"
    error_type: ErrorType = Field(
        description="`invalid_argument` for a fault in the call itself, `upstream` "
        "for a failure of something the call depended on"
    )
    error_details: str


class NotAvailableResult(BaseModel):
    """The request is well-formed but outside what this deployment can model.

    Distinct from :class:`ErrorResult` on purpose: nothing has gone wrong, so the
    agent must report a scope limit rather than a system fault. The two roofs
    GR2L declines are the standing case — the gravel roof has no substrate to
    simulate and the wetland's ponded storage has no water-content contract —
    while both stay queryable like any other roof's.
    """

    status: Literal["not_available"] = "not_available"
    reason: str


class ReferenceCardResult(BaseModel):
    """One reference card, as :func:`lookup_reference` hands it to the agent.

    The card's own field names, deliberately: a person reading the YAML in
    ``knowledge/cards/`` and a model reading this payload see the same four
    blocks under the same four names, so a card's prose about "the values block"
    means something at both ends. ``id`` becomes ``topic`` — the one rename —
    because that is the argument the agent named to get here, and card recall is
    scored over exactly those names (``agent_architecture.md`` §7).

    There is no ``not_available`` counterpart. A known topic returns its card
    whole and a card states its own limits inside ``not_applicable``; typing that
    abstention at the tool level would collapse it into the already-tested
    "relay a typed ``not_available``" behaviour and cost the catalog its
    strongest hallucination probe (``decisions.md`` § Retrieval).
    """

    status: Literal["success"] = "success"
    topic: str = Field(description="The card that was read — the card's own `id`")
    title: str
    provenance: Provenance = Field(
        description="`rendered` means a test holds this card's numbers equal to the "
        "deployed controller's own constants; `static` means they are pinned "
        "elsewhere — the database hash, or the roof table"
    )
    text: str = Field(description="The card's hand-written prose, carrying no numerals")
    values: dict[str, Any] = Field(
        default_factory=dict, description="The numbers the card states, keyed by name"
    )
    applies_to: list[str] = Field(
        default_factory=list,
        description="Roof segments this card's values hold for; empty where the card "
        "is a property of the site or the weather rather than of a segment",
    )
    not_applicable: dict[str, str] = Field(
        default_factory=dict,
        description="Roof segment → why this card does not cover it. Returned with "
        "the card whatever `roof` was asked for, and never filtered: a segment "
        "listed here has no such value to be inferred from the segments that do",
    )
    roof: str | None = Field(
        default=None,
        description="The canonical segment the call was scoped to, resolved from "
        "whatever spelling was passed; null when the card was read unscoped",
    )
    values_scoped_to_roof: bool = Field(
        default=False,
        description="Whether `values` was narrowed to `roof`. False with a `roof` "
        "given means the card keeps no separate numbers for that segment — either "
        "it states one set for the site, or the segment is in `not_applicable`",
    )


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
    balance. Every roof this tool serves converts between the two — the one whose
    ponded storage had no θ equivalent, the wetland, is declined at entry instead
    (``gr2l_client.NON_MODELLABLE_ROOFS``), so the field is optional only for the
    null ``Ssub`` an older model build could return.
    """

    swc_pct: float | None = Field(
        default=None, description="Substrate volumetric water content, %θ"
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
        default=None, description="Driest day's water content, %θ"
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


class IrrigationDose(BaseModel):
    """How much water the site applies when the answer is yes.

    Stated **alongside** the decision, never derived from it: the deployed
    algorithm has no volume calculation, so quoting anything computed here would
    score the agent against arithmetic nobody runs
    (``decisions.md`` § The irrigation calculator). ``dose_mm`` is owed by the
    site and is null until it arrives; the valve minutes are what a disclosure
    can honestly state meanwhile.
    """

    dose_mm: float | None = Field(
        default=None, description="The roof's dose as a depth, mm — null until the site states it"
    )
    valve_minutes: int = Field(
        description="How long the deployed controller opens this roof's valve"
    )


class IrrigationFeatures(BaseModel):
    """The four quantities the decision turned on, and the windows they were read over.

    Soil moisture is reported in %θ whichever unit the balance ran in: millimetres
    stay internal and the site's own unit is what a surface speaks
    (``agent_architecture.md`` §3.5).
    """

    min_swc_pct: float = Field(description="Driest point of the decision window, %θ")
    max_temperature_c: float = Field(description="Warmest day of the decision window, °C")
    will_reach_capacity: bool = Field(
        description="The roof is expected to refill on its own inside the refill horizon"
    )
    refill_expected_on: str | None = Field(
        default=None, description="First day the roof is expected to reach field capacity"
    )
    decision_horizon_hours: int = Field(
        description="How far ahead moisture and heat were read"
    )
    refill_horizon_hours: int = Field(description="How far ahead a refill was looked for")


class IrrigationResult(BaseModel):
    """Result of :func:`calc_irrigation` — a decision, never a volume."""

    status: Literal["success"] = "success"
    roof_type: str
    irrigate: bool = Field(description="Whether the deployed rule recommends irrigating")
    reason: str = Field(
        description="Which rung of the priority ladder decided it — a code, not prose"
    )
    inputs: Literal["modelled", "stated"] = Field(
        description="`modelled` ran the bucket over the roof's own sensor and the "
        "forecast; `stated` applied the rule to values the caller supplied, with no "
        "simulation. An answer says which"
    )
    features: IrrigationFeatures
    dose: IrrigationDose
    seed: SwcSeed | None = Field(
        default=None,
        description="Where the modelled run's day-1 soil moisture came from; null on "
        "the stated path, which starts from the caller's own value",
    )
    weather_source: Literal["station", "archive"] | None = Field(
        default=None,
        description="Which source forced the modelled run — `station` means the site's "
        "own instruments; null on the stated path, which fetches nothing",
    )
    window_start: str | None = Field(
        default=None, description="First day of the simulated window (modelled runs only)"
    )
    window_end: str | None = Field(
        default=None, description="Last day of the simulated window (modelled runs only)"
    )


class SeriesSpec(BaseModel):
    """One series a plot request declares — **a source, never data**.

    The agent names where a series comes from and which quantity to draw; the
    tool fetches it through the seams the other tools use
    (``agent_architecture.md`` §3.6). Passing the values in as an argument is
    what this shape exists to prevent: it drives the data through the LLM and
    turns the family's argument checking into a test of whether the model
    retyped forty floats correctly (``decisions.md`` § Plotting).

    ``extra="forbid"``: a field this model does not know is a typed
    ``invalid_argument`` naming what was valid, not a silently dropped selector
    that changes which series is drawn.
    """

    model_config = ConfigDict(extra="forbid")

    source: Literal["measured", "weather", "model"] = Field(
        description="`measured` reads the site's own record, `weather` the daily "
        "weather the other tools are forced by, `model` a GR2L run"
    )
    variable: str = Field(description="The quantity to draw, out of the closed vocabulary")
    table: str | None = Field(
        default=None, description="Which of the five tables (`measured` series only)"
    )
    roof: str | None = Field(
        default=None,
        description="The roof segment, in any spelling the site uses. Required for a "
        "`measured` series over a per-roof table; a station series takes none",
    )


class PlotSeries(BaseModel):
    """One series of the **resolved** spec: what the agent asked for, plus what followed.

    The first four fields are the agent-supplied half — the surface §7 scores —
    and the rest is derived in code from the variable. Unit, axis and
    aggregation are echoed so a reader can see which operator ran, never so a
    caller can choose one (``agent_architecture.md`` §3.6).
    """

    source: Literal["measured", "weather", "model"]
    variable: str
    table: str | None = Field(default=None, description="The table a `measured` series read")
    roof: str | None = Field(default=None, description="The canonical segment, resolved from any alias")
    column: str | None = Field(
        default=None,
        description="The column the variable and the roof resolved to — the record's own "
        "name, so an answer can be traced back to it",
    )
    quantity: Literal["flux", "state"] = Field(
        description="`flux` accumulates over the sampling interval, `state` is sampled at "
        "an instant. This is what decides the operator"
    )
    aggregation: Literal["sum", "mean"] = Field(
        description="The operator that aggregates this series — derived from `quantity`, "
        "never chosen by the caller: fluxes sum, states average"
    )
    unit: str
    axis: str = Field(
        description="Series sharing an axis are drawn against one scale. Two variables in "
        "the same unit but of different quantities do not share one"
    )
    note: str | None = Field(
        default=None,
        description="What a reader of this series has to know to read it correctly — the "
        "`radiation` table's hour offset, or outflow's litres-are-millimetres relabel. "
        "State it in the answer whenever it is present",
    )


class PlotResult(BaseModel):
    """Result of :func:`plot_timeseries` — the resolved spec, and no series.

    The series' consumer is the renderer, not the LLM
    (``agent_architecture.md`` §3.6): what comes back here is what the agent
    asked for, resolved, so it can say what was drawn without transcribing it.
    """

    status: Literal["success"] = "success"
    kind: Literal["line", "bar", "model_overlay", "diff"]
    start: str = Field(description="First day drawn, resolved to an absolute date")
    end: str = Field(description="Last day drawn, resolved to an absolute date")
    resolution: Literal["half_hourly", "daily"] = Field(
        description="The record's own half-hourly sampling, or calendar days at the "
        "site's timezone. Daily is forced whenever a `measured` series shares the plot "
        "with a daily source, since the two cannot be drawn against one x-axis otherwise"
    )
    series: list[PlotSeries]


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
