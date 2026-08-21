"""ADK tool: time series drawn from declared sources, over a closed vocabulary.

The agent names **where** a series comes from and **which quantity** to draw;
this module fetches it through the seams the other tools already use and returns
the resolved spec. Nothing about the data itself is an argument, and nothing
about the data comes back to the model: the series' consumer is the renderer
(``agent_architecture.md`` §3.6, ``decisions.md`` § Plotting).

**The `measured` vocabulary is a table, and it is closed.** Twelve variables over
the five as-of tables, each carrying its column, whether it is a flux or a state,
the operator that follows from that, its unit and its axis. A variable that is
not in it cannot be drawn — there is no free SQL here, so plotting reaches less
of the database than the text-to-SQL agent does, which is a disclosed scope limit
(§8) rather than a gap to widen. Three properties of the table are worth stating
because they are what keep it one table rather than four:

*The column is resolved through* :mod:`roofs` *and never re-listed here.* A
per-roof variable carries no column at all — ``ROOFS[roof].columns[table]`` is
the column, and which roofs a table even has follows from the same place, so the
semi-intensive roof is absent from ``outflow`` and ``radiation`` here for the one
reason it is absent there: it has no lysimeter and no mast. ``radiation`` carries
a mast *suffix*, joined to the roof's prefix by :func:`roofs.radiation_column`.
Only ``wetter`` names a column outright, because the station belongs to no roof.

*The operator is derived, and there is no ``agg`` argument to override it.* A
flux is a quantity the sampling interval **accumulates** — rain that fell in it,
runoff that drained through it — and it sums; a state is a quantity sampled *at*
an instant — a water content, a temperature, an irradiance — and it averages. One
plot-level operator could not serve a mixed plot anyway: precipitation sums where
soil moisture averages, on the same chart (``decisions.md`` § Plotting). The
distinction is drawn on accumulation rather than on the physical word "flux"
because W/m² is a rate reported at an instant: summing 48 half-hourly readings of
it would report 48× the day's mean under a unit nobody uses.

*Area-normalized outflow is not in the vocabulary.* Every lysimeter collects 1 m²
(:data:`roofs.LYSIMETER_AREA_M2`), so a litre of collected runoff is already a
millimetre of depth: the entry relabels the unit and the values are served
unscaled. An area factor here would be the bug that constant exists to prevent.

**Two things the vocabulary discloses rather than repairs.** ``radiation`` is
stamped an hour behind the other four tables and the pinned file is not rebuilt
(``decisions.md`` § The ``radiation`` timestamp offset), so every ``radiation``
entry carries the offset as a ``note`` that rides out with the resolved series —
§8 requires the disclosure wherever the table is reachable, and a reader who does
not know reads the misalignment as physics. ``outflow``'s note is the litres
relabel, for the same reason: an unstated relabel is indistinguishable from a
conversion nobody applied.

**Two `wetter` columns are deliberately outside the vocabulary.** ``Tmax`` needs
a ``max`` and ``windspeed`` needs the logger's sentinel filtered, and both of
those already exist, once, in the station derivation
(:mod:`weather_station`) — which this tool reaches as a ``weather`` series, where
the day's maximum and the day's wind arrive as ``tx`` and ``w``. Serving them
here under a sum-or-mean would name a mean of half-hourly maxima "the daily
maximum"; serving them here under a copy of the sentinel rule would be the second
place for that rule to drift.

**The window vocabulary is the weather tool's**, not a second one: ``start_date``
/ ``end_date`` / ``past_days`` / ``forecast_days`` through
:func:`weather_client.resolve_window`, against ``ctx.as_of``. Dropping the
relative forms so the arguments were always comparable as written was considered
and rejected — it reverses a decision already taken for the weather tool and
splits one vocabulary in two — and the scorer resolves the *argument* through
this same layer-1 resolver instead (``decisions.md`` § Plotting).

**The two live sources are the other tools' own seams, reached through ``ctx``.**
A ``weather`` series is ``ctx.weather``'s daily row and a ``model`` series is
:func:`gr2l.run_roof_model` — the same composite, the same response cache, the
same window resolution and the same ``min(window_start, as_of)`` seed rule the
standalone tools run under, because they are the same functions. That is what
§3.6 makes this tool's self-containment conditional on: no DuckDB connection of
its own, no HTTP client of its own, three sources and one context. A plot in
replay therefore issues no live call for exactly the reason a modelled case does
not — both read the cache the case committed.
"""

import asyncio
import dataclasses
import json
from collections.abc import Awaitable, Callable, Sequence
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

import structlog
from google.adk.tools.tool_context import ToolContext
from pydantic import ValidationError

from water_assistant_agent.assistant.context import AS_OF_TABLES
from water_assistant_agent.assistant.ports import ReadOnlyWarehouseQuery
from water_assistant_agent.assistant.tools.gr2l import (
    ForcingError,
    ModelRun,
    ModelWeatherError,
    normalize_forcings,
    run_roof_model,
)
from water_assistant_agent.assistant.tools.gr2l_client import Gr2lConfigError
from water_assistant_agent.assistant.tools.roofs import (
    ROOFS,
    RoofSegment,
    radiation_column,
    resolve_roof,
    roofs_with_column,
)
from water_assistant_agent.assistant.tools.schemas import (
    ErrorResult,
    NotAvailableResult,
    PlotResult,
    PlotSeries,
    PlotSeriesStats,
    SeriesSpec,
    WeatherResult,
)
from water_assistant_agent.assistant.tools.site import site_day_expr, site_timestamp_expr
from water_assistant_agent.assistant.tools.swc import SwcUnavailableError
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

PlotTimeseriesTool = Callable[..., Awaitable[dict[str, Any]]]
"""What :func:`make_plot_timeseries_tool` returns: the ADK-facing plot tool."""

Resolution = Literal["half_hourly", "daily"]

PLOT_KINDS: tuple[str, ...] = ("line", "bar", "model_overlay", "diff")
"""The chart shapes the renderer knows. Unscored — §7 fixes the scored surface
to source, variable, roof and the resolved range — but still closed, because a
kind the renderer cannot draw is a plot that never appears.
"""

# Axis identities. Series sharing one are drawn against one scale, so this is a
# grouping decision rather than a restatement of the unit: rain and runoff share
# an axis across two tables, while relative humidity and soil moisture — both
# percentages — do not, and a surface temperature in kelvin does not share the
# air's Celsius axis.
AXIS_WATER_DEPTH = "water_depth_mm"
AXIS_WATER_CONTENT = "water_content_pct"
AXIS_TEMPERATURE = "temperature_c"
AXIS_SURFACE_TEMPERATURE = "surface_temperature_k"
AXIS_IRRADIANCE = "irradiance_w_m2"
AXIS_HUMIDITY = "relative_humidity_pct"
AXIS_WIND = "wind_speed_km_h"
AXIS_RADIATION_SUM = "radiation_j_cm2_day"
"""A day's total radiation, which is not the irradiance axis restated.

``wetter``'s ``Rad_SW`` is a W/m² reading at an instant and the weather row's
``gs`` is J/cm² accumulated over a day: same physics, different quantity, three
orders of magnitude apart. Drawn against one scale the instantaneous series
would be a flat line along the axis.
"""

AXIS_WATER_STORAGE = "water_storage_mm"
"""What the roof is holding, as against what moved through it.

Both are millimetres, so this is the rain/humidity rule again one layer up: a
substrate storage of 30 mm and a day's 3 mm of runoff share a unit and not a
scale, and putting the state on the flux's axis would flatten every day of
runoff the chart exists to show.
"""

_OUTFLOW_NOTE = (
    "Lysimeter outflow is recorded in litres and reported in millimetres: every "
    "collection area is 1 m², so a litre is already a depth and no area factor is "
    "applied. The values are the record's own, relabelled."
)

_RADIATION_NOTE = (
    "The `radiation` table is stamped one hour behind the other four and is served "
    "uncorrected, so a radiation series drawn beside another table's is misaligned by "
    "two half-hourly rows — say so rather than reading the offset as physics. The "
    "table also covers only 2025-03-01 to 2025-10-01."
)


_SEED_DAY_NOTE = (
    "Day 1 of a modelled run only seeds the stores, so this quantity is not computed "
    "for it and the series opens on day 2 — the same reason a window's retention "
    "excludes its first day's runoff."
)


def aggregation_for(quantity: str) -> Literal["sum", "mean"]:
    """The operator a quantity is aggregated by: fluxes sum, states average.

    The rule of §3.6, written once and shared by all three vocabularies, so a
    ``model`` series' runoff and a ``measured`` series' runoff cannot end up
    aggregated differently because two tables each spelled the rule out.
    """
    return "sum" if quantity == "flux" else "mean"


@dataclasses.dataclass(frozen=True, slots=True)
class MeasuredVariable:
    """One row of the closed vocabulary: what a ``measured`` series may draw.

    :attr:`station_column` and :attr:`mast_suffix` are the two exceptions to
    "the column comes from :mod:`roofs`", and each is empty on the other's
    tables: a per-roof variable sets neither and takes
    ``ROOFS[roof].columns[table]`` whole.
    """

    table: str
    quantity: Literal["flux", "state"]
    """Whether the sampling interval accumulates this quantity or samples it."""

    unit: str
    axis: str
    station_column: str | None = None
    """The ``wetter`` column, named here because the station belongs to no roof."""

    mast_suffix: str | None = None
    """The ``radiation`` mast suffix; the prefix is the roof's own (:mod:`roofs`)."""

    note: str | None = None
    """What a reader of this series has to be told — carried out with the spec."""

    @property
    def aggregation(self) -> Literal["sum", "mean"]:
        """The operator this variable is aggregated by. Derived, never chosen."""
        return aggregation_for(self.quantity)

    @property
    def per_roof(self) -> bool:
        """True when the series is one roof's, so a ``roof`` selector is required."""
        return self.station_column is None


MEASURED_VOCABULARY: dict[tuple[str, str], MeasuredVariable] = {
    # Per-roof tables: no column here, because `roofs.py` has it.
    ("swc", "soil_moisture"): MeasuredVariable(
        table="swc", quantity="state", unit="%θ", axis=AXIS_WATER_CONTENT
    ),
    ("tsoil", "soil_temperature"): MeasuredVariable(
        table="tsoil", quantity="state", unit="°C", axis=AXIS_TEMPERATURE
    ),
    ("outflow", "outflow"): MeasuredVariable(
        table="outflow",
        quantity="flux",
        unit="mm",
        axis=AXIS_WATER_DEPTH,
        note=_OUTFLOW_NOTE,
    ),
    # The station: one set of columns for the whole site, so these name theirs.
    ("wetter", "precipitation"): MeasuredVariable(
        table="wetter",
        quantity="flux",
        unit="mm",
        axis=AXIS_WATER_DEPTH,
        station_column="Rain",
    ),
    ("wetter", "air_temperature"): MeasuredVariable(
        table="wetter",
        quantity="state",
        unit="°C",
        axis=AXIS_TEMPERATURE,
        station_column="Tmean",
    ),
    ("wetter", "relative_humidity"): MeasuredVariable(
        table="wetter",
        quantity="state",
        unit="%",
        axis=AXIS_HUMIDITY,
        station_column="RH",
    ),
    ("wetter", "shortwave_radiation"): MeasuredVariable(
        table="wetter",
        quantity="state",
        unit="W/m²",
        axis=AXIS_IRRADIANCE,
        station_column="Rad_SW",
    ),
    # The masts: a suffix each, joined to the roof's prefix by `roofs.py`.
    ("radiation", "shortwave_down"): MeasuredVariable(
        table="radiation",
        quantity="state",
        unit="W/m²",
        axis=AXIS_IRRADIANCE,
        mast_suffix="SWdown",
        note=_RADIATION_NOTE,
    ),
    ("radiation", "shortwave_up"): MeasuredVariable(
        table="radiation",
        quantity="state",
        unit="W/m²",
        axis=AXIS_IRRADIANCE,
        mast_suffix="SWup",
        note=_RADIATION_NOTE,
    ),
    ("radiation", "longwave_down"): MeasuredVariable(
        table="radiation",
        quantity="state",
        unit="W/m²",
        axis=AXIS_IRRADIANCE,
        mast_suffix="LWdown",
        note=_RADIATION_NOTE,
    ),
    ("radiation", "longwave_up"): MeasuredVariable(
        table="radiation",
        quantity="state",
        unit="W/m²",
        axis=AXIS_IRRADIANCE,
        mast_suffix="LWup",
        note=_RADIATION_NOTE,
    ),
    ("radiation", "surface_temperature"): MeasuredVariable(
        table="radiation",
        quantity="state",
        unit="K",
        axis=AXIS_SURFACE_TEMPERATURE,
        mast_suffix="TSFC",
        note=_RADIATION_NOTE,
    ),
    ("radiation", "surface_temperature_corrected"): MeasuredVariable(
        table="radiation",
        quantity="state",
        unit="K",
        axis=AXIS_SURFACE_TEMPERATURE,
        mast_suffix="TSFCkorr",
        note=_RADIATION_NOTE,
    ),
}
"""The closed vocabulary, keyed by ``(table, variable)``.

Keyed by the pair rather than by the variable alone because the table is part of
what the agent declares and part of what is scored: two shortwave series from
two different instruments are two different series, and naming the table is what
distinguishes them.
"""

MEASURED_TABLES: tuple[str, ...] = tuple(
    table for table in AS_OF_TABLES if any(key[0] == table for key in MEASURED_VOCABULARY)
)
"""The tables a ``measured`` series can reach, in the as-of connection's order.

Read off the vocabulary and filtered through :data:`~..context.AS_OF_TABLES`, so
a variable over a table no as-of view bounds cannot appear here and then read
past a case's cut.
"""


@dataclasses.dataclass(frozen=True, slots=True)
class DailyVariable:
    """One row of the ``weather`` or ``model`` vocabulary — a daily row's field.

    The same four columns :class:`MeasuredVariable` carries, minus the three that
    exist only to resolve a database column: a daily series is a field of a row
    the other tools already return, so its name *is* its selector. The identity
    it adds is the one a chart needs — the quantity, and with it the operator, the
    unit and the axis.
    """

    quantity: Literal["flux", "state"]
    """Whether the day accumulates this quantity or reports it as a state."""

    unit: str
    axis: str
    note: str | None = None
    """What a reader of this series has to be told — carried out with the spec."""

    @property
    def aggregation(self) -> Literal["sum", "mean"]:
        """The operator this variable is aggregated by. Derived, never chosen."""
        return aggregation_for(self.quantity)


WEATHER_VOCABULARY: dict[str, DailyVariable] = {
    "precip": DailyVariable(quantity="flux", unit="mm", axis=AXIS_WATER_DEPTH),
    "tm": DailyVariable(quantity="state", unit="°C", axis=AXIS_TEMPERATURE),
    "tx": DailyVariable(quantity="state", unit="°C", axis=AXIS_TEMPERATURE),
    "tn": DailyVariable(quantity="state", unit="°C", axis=AXIS_TEMPERATURE),
    "rf": DailyVariable(quantity="state", unit="%", axis=AXIS_HUMIDITY),
    "w": DailyVariable(quantity="state", unit="km/h", axis=AXIS_WIND),
    "gs": DailyVariable(quantity="flux", unit="J/cm²/day", axis=AXIS_RADIATION_SUM),
}
"""What a ``weather`` series may draw: every field of ``DailyWeatherRow`` but ``Date``.

Keyed by the row's **own** field names rather than by prettier ones, so a plot
declares ``tx`` where a forcing forces ``tx`` and the weather tool reports
``tx`` — one vocabulary across the three, which is the rule ``forcings`` is
built on too (``decisions.md`` § GR2L argument surface). The daily maximum and
the day's wind are here, and deliberately absent from the ``measured``
vocabulary: both need a derivation the station half already owns, and serving
them twice would be the second place for it to drift.
"""

MODEL_VOCABULARY: dict[str, DailyVariable] = {
    "swc_pct": DailyVariable(quantity="state", unit="%θ", axis=AXIS_WATER_CONTENT),
    "Ssub": DailyVariable(quantity="state", unit="mm", axis=AXIS_WATER_STORAGE),
    "Sret": DailyVariable(quantity="state", unit="mm", axis=AXIS_WATER_STORAGE),
    "OUT": DailyVariable(
        quantity="flux", unit="mm", axis=AXIS_WATER_DEPTH, note=_SEED_DAY_NOTE
    ),
    "ET": DailyVariable(quantity="flux", unit="mm", axis=AXIS_WATER_DEPTH),
    "ET_PM": DailyVariable(quantity="flux", unit="mm", axis=AXIS_WATER_DEPTH),
    "Qdown": DailyVariable(
        quantity="flux", unit="mm", axis=AXIS_WATER_DEPTH, note=_SEED_DAY_NOTE
    ),
    "Qup": DailyVariable(
        quantity="flux", unit="mm", axis=AXIS_WATER_DEPTH, note=_SEED_DAY_NOTE
    ),
}
"""What a ``model`` series may draw: every field of ``GreenRoofDay`` but ``Date``.

``swc_pct`` shares :data:`AXIS_WATER_CONTENT` with the ``swc`` sensor and ``OUT``
shares :data:`AXIS_WATER_DEPTH` with the lysimeter, which is what makes a
``model_overlay`` a comparison rather than two charts in one frame: the modelled
and the measured quantity are the same quantity, in the same unit, against the
same scale. The three that open on day 2 carry that as a note rather than as a
silent hole — the seed day computes no flux (``gr2l_tool.md``).
"""


class PlotVocabularyError(ValueError):
    """A series that the closed vocabulary cannot draw — an argument fault, pre-I/O.

    Raised before any query, so the message names what would have been valid and
    the agent can correct the call. Deliberately **not** a ``not_available``: the
    one scope limit this tool signals is a ``model`` series for a roof outside the
    model (§3.6), and typing an unreachable column as an abstention too would
    make the false-abstention rate uninterpretable
    (``decisions.md`` § Typed abstention).
    """


def measured_variable(table: str | None, variable: str) -> MeasuredVariable:
    """The vocabulary entry for *variable* in *table*.

    Raises:
        PlotVocabularyError: the table is not one of the five, or the table has
            no such variable. The message lists what it does have.
    """
    if table is None:
        raise PlotVocabularyError(
            f"A measured series must name a table. Valid tables: {', '.join(MEASURED_TABLES)}."
        )
    if table not in MEASURED_TABLES:
        raise PlotVocabularyError(
            f"Unknown table {table!r}. Valid tables: {', '.join(MEASURED_TABLES)}."
        )
    entry = MEASURED_VOCABULARY.get((table, variable))
    if entry is None:
        valid = ", ".join(
            name for entry_table, name in MEASURED_VOCABULARY if entry_table == table
        )
        raise PlotVocabularyError(
            f"Unknown variable {variable!r} for table {table!r}. "
            f"Valid variables there: {valid}."
        )
    return entry


def measured_column(entry: MeasuredVariable, roof: str | None) -> tuple[str, RoofSegment | None]:
    """The column *entry* reads for *roof*, and the segment that resolved to.

    The three shapes the vocabulary's own docstring names, in one place: the
    station's own column, a mast prefix joined to a suffix, or the roof's column
    in the table. Only the first is written in this module.

    Raises:
        PlotVocabularyError: a roof on a station series, a missing roof on a
            per-roof series, an unrecognized spelling, or a roof the table has no
            column for — the semi-intensive roof's absent lysimeter and mast.
    """
    if not entry.per_roof:
        if roof is not None:
            raise PlotVocabularyError(
                f"The {entry.table!r} station serves the whole site, so a series over it "
                f"takes no roof; got {roof!r}."
            )
        # `station_column` is set exactly when `per_roof` is false.
        return str(entry.station_column), None

    instrumented = roofs_with_column(entry.table)
    if roof is None:
        raise PlotVocabularyError(
            f"A {entry.table!r} series is one roof's, so it needs a roof. "
            f"Instrumented there: {', '.join(instrumented)}."
        )
    segment = resolve_roof(roof)
    if segment is None:
        raise PlotVocabularyError(
            f"Unknown roof {roof!r}. Valid roofs: {', '.join(ROOFS)}."
        )
    if entry.table not in segment.columns:
        raise PlotVocabularyError(
            f"The {segment.name} roof is not instrumented in {entry.table!r}, so it has no "
            f"{entry.table} series. Instrumented there: {', '.join(instrumented)}."
        )
    if entry.mast_suffix is not None:
        return radiation_column(segment, entry.mast_suffix), segment
    return segment.columns[entry.table], segment


def daily_variable(source: str, variable: str) -> DailyVariable:
    """The vocabulary entry for *variable* on the ``weather`` or ``model`` source.

    Raises:
        PlotVocabularyError: that source has no such field. The message lists
            the ones it has, under the names the other tools already report them
            by — the agent asks for ``tx``, not for "maximum temperature".
    """
    vocabulary = WEATHER_VOCABULARY if source == "weather" else MODEL_VOCABULARY
    entry = vocabulary.get(variable)
    if entry is None:
        raise PlotVocabularyError(
            f"Unknown variable {variable!r} for a {source!r} series. "
            f"Valid variables there: {', '.join(vocabulary)}."
        )
    return entry


@dataclasses.dataclass(frozen=True, slots=True)
class PreparedSeries:
    """One series proved drawable, with everything the fetch needs already resolved.

    The whole of the argument check happens here and the whole of the I/O happens
    after it, which is what lets a plot that cannot be drawn cost nothing: no
    query, and — the reason it matters — no GR2L request, so a declined plot
    never records a cache entry for a run nobody will read.
    """

    spec: SeriesSpec
    variable: MeasuredVariable | DailyVariable
    column: str | None = None
    """The database column a ``measured`` series reads; ``None`` for a daily source."""

    roof: RoofSegment | None = None
    """The canonical segment, resolved from whatever alias the agent used."""

    forcings: dict[str, dict[str, float]] | None = None
    """A ``model`` series' counterfactual, already checked against the window.

    Normalized here rather than inside the run so a forcing naming a day outside
    the chart is caught with the other argument faults — before the first fetch,
    not after some other series has already been drawn.
    """


def _reject_foreign_selectors(spec: SeriesSpec) -> None:
    """Refuse a selector that belongs to a different source.

    ``extra="forbid"`` catches a field no series has; this catches a field some
    series has and *this* one does not — an albedo on a rain gauge, a table on a
    modelled roof. Both are the same fault to the agent, and neither is worth a
    silent drop that would change which series is drawn.
    """
    modelling = {
        "initial_soil_moisture_pct": spec.initial_soil_moisture_pct,
        "albedo": spec.albedo,
        "forcings": spec.forcings,
    }
    named = sorted(field for field, value in modelling.items() if value is not None)
    if spec.source != "model" and named:
        raise PlotVocabularyError(
            f"{', '.join(named)} belong{'s' if len(named) == 1 else ''} to a 'model' "
            f"series, and this one is {spec.source!r}. A measured or weather series is "
            "the record as it stands; there is nothing to assume about it."
        )
    if spec.source != "measured" and spec.table is not None:
        raise PlotVocabularyError(
            f"Only a 'measured' series names a table; a {spec.source!r} series got "
            f"table={spec.table!r}."
        )


def prepare_series(spec: SeriesSpec, start: str, end: str) -> PreparedSeries:
    """Resolve *spec* against the vocabularies, or say why it cannot be drawn.

    Raises:
        PlotVocabularyError: the series is outside the closed vocabulary, or
            carries a selector its source does not take.
        ForcingError: a counterfactual that names a day outside ``start..end``.
    """
    _reject_foreign_selectors(spec)

    if spec.source == "measured":
        entry = measured_variable(spec.table, spec.variable)
        column, segment = measured_column(entry, spec.roof)
        return PreparedSeries(spec=spec, variable=entry, column=column, roof=segment)

    daily = daily_variable(spec.source, spec.variable)
    if spec.source == "weather":
        if spec.roof is not None:
            raise PlotVocabularyError(
                "The weather is the site's, not a roof's, so a 'weather' series takes no "
                f"roof; got {spec.roof!r}. For one roof's own record, draw a 'measured' "
                "series."
            )
        return PreparedSeries(spec=spec, variable=daily)

    if spec.roof is None:
        raise PlotVocabularyError(
            "A 'model' series simulates one roof, so it needs a roof. "
            f"Valid roofs: {', '.join(ROOFS)}."
        )
    segment = resolve_roof(spec.roof)
    if segment is None:
        raise PlotVocabularyError(
            f"Unknown roof {spec.roof!r}. Valid roofs: {', '.join(ROOFS)}."
        )
    # The window's own check, run by the water-balance tool's own validator: a
    # forcing is addressed by day, and a day the chart does not cover is an
    # override that would silently do nothing (`decisions.md` § GR2L argument surface).
    forcings = normalize_forcings(spec.forcings, start, end) if spec.forcings else None
    return PreparedSeries(spec=spec, variable=daily, roof=segment, forcings=forcings)


def plot_resolution(sources: Sequence[str]) -> Resolution:
    """The resolution every series in a plot with these *sources* is drawn at.

    Half-hourly is the record's own sampling and is kept while the plot is
    ``measured`` throughout. The moment a ``weather`` or ``model`` series joins
    it, the plot is mixed — those two are daily, ``swc`` and ``wetter`` are
    half-hourly — and the measured half is aggregated to Europe/Berlin calendar
    days by its variable's own operator, because two series cannot share an
    x-axis at two resolutions (``agent_architecture.md`` §3.6). A plot with no
    measured series at all is daily because both live sources are.

    Decided over the whole plot, before any series is fetched: the operator is a
    property of the variable, but the resolution is a property of the chart.
    """
    return "half_hourly" if set(sources) == {"measured"} else "daily"


@dataclasses.dataclass(frozen=True, slots=True)
class ResolvedSeries:
    """One fetched series: the spec the agent gets back, and the points it does not.

    The split is the point (``agent_architecture.md`` §3.6). :attr:`spec` is the
    model-visible half; :attr:`points` reaches the renderer without passing
    through the model.
    """

    spec: PlotSeries
    points: list[tuple[str, float]]
    """``(timestamp, value)``, ascending. Stamped in the site's own timezone, so a
    point's label and the day it is counted under agree."""


def _measured_query(
    entry: MeasuredVariable,
    column: str,
    start: str,
    end: str,
    resolution: Resolution,
) -> str:
    """The fixed query, in one place, with the vocabulary supplying the identifiers.

    No LLM SQL reaches this module: the table comes from :data:`MEASURED_TABLES`,
    the column from :func:`measured_column` (so from :mod:`roofs`), the operator
    from the variable, and the window from :func:`resolve_window`'s already-parsed
    dates. Rows are read through the caller's executor, so a case sees its own
    as-of view and nothing past its cut.
    """
    day = site_day_expr()
    window = f"{day} BETWEEN DATE '{start}' AND DATE '{end}'"
    conditions = f'"{column}" IS NOT NULL AND {window}'
    if resolution == "daily":
        return (
            f'SELECT {day} AS at, {entry.aggregation}("{column}") AS value '  # noqa: S608 - identifiers from the vocabulary
            f"FROM {entry.table} WHERE {conditions} GROUP BY 1 ORDER BY 1"
        )
    # The window is still bounded by the site's day, so a half-hourly series and
    # the daily one it may be redrawn as cover exactly the same span.
    return (
        f'SELECT {site_timestamp_expr()} AS at, "{column}" AS value '  # noqa: S608 - identifiers from the vocabulary
        f"FROM {entry.table} WHERE {conditions} ORDER BY 1"
    )


def _window_days(start: str, end: str) -> list[str]:
    """Every calendar day of ``[start, end]``, inclusive, as ISO strings."""
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    return [
        (first + timedelta(days=offset)).isoformat()
        for offset in range((last - first).days + 1)
    ]


def _summarize(points: list[tuple[str, float]], quantity: str) -> PlotSeriesStats:
    """What the drawn values amount to — the only numbers the model is given.

    ``total`` follows the same accumulate-or-sample distinction the operator
    does: a window's rain and runoff have a total, a water content and a
    temperature do not, and reporting one would name a sum of states a quantity.
    """
    values = [value for _, value in points]
    if not values:
        return PlotSeriesStats(points=0)
    return PlotSeriesStats(
        points=len(values),
        first=points[0][0],
        last=points[-1][0],
        min=round(min(values), 3),
        max=round(max(values), 3),
        mean=round(sum(values) / len(values), 3),
        total=round(sum(values), 3) if quantity == "flux" else None,
    )


def _coverage(points: list[tuple[str, float]], start: str, end: str) -> tuple[int, bool]:
    """``(gaps, truncated)`` for a series drawn over ``[start, end]``.

    Both are read off the days the series actually has, which is why the flags
    mean the same thing at either resolution and from any of the three sources: a
    half-hourly stamp and a daily one begin with the same ten characters, and a
    day with no point is a day with no point whether the sensor was down, the
    record had not started, or the model does not report that quantity on its
    seed day.

    They say different things and both are needed. *Gaps* is a hole a reader
    would otherwise interpolate across; *truncated* is the series ending before
    the window does — the record running out, or a case's as-of cut — which is
    the one a "the last week of June" question can be silently wrong about.
    """
    covered = {at[:10] for at, _ in points}
    gaps = sum(1 for day in _window_days(start, end) if day not in covered)
    return gaps, not covered or max(covered) < end


def _resolved_series(
    prepared: PreparedSeries,
    points: list[tuple[str, float]],
    start: str,
    end: str,
    **echoed: Any,
) -> ResolvedSeries:
    """Pair *points* with the resolved spec that describes them.

    One builder for all three sources, because the derived half of the spec is
    one derivation: the operator, the unit, the axis and the note come off the
    vocabulary row, and the coverage flags and the statistics come off the points
    (``agent_architecture.md`` §3.6). Only *echoed* differs per source — the
    column a ``measured`` series read, the modelling arguments a ``model`` series
    was given.
    """
    entry = prepared.variable
    gaps, truncated = _coverage(points, start, end)
    return ResolvedSeries(
        spec=PlotSeries(
            source=prepared.spec.source,
            variable=prepared.spec.variable,
            roof=prepared.roof.name if prepared.roof is not None else None,
            quantity=entry.quantity,
            aggregation=entry.aggregation,
            unit=entry.unit,
            axis=entry.axis,
            note=entry.note,
            gaps=gaps,
            truncated=truncated,
            stats=_summarize(points, entry.quantity),
            **echoed,
        ),
        points=points,
    )


def fetch_measured_series(
    executor: ReadOnlyWarehouseQuery,
    prepared: PreparedSeries,
    start: str,
    end: str,
    resolution: Resolution,
) -> ResolvedSeries:
    """Read one ``measured`` series and echo what the vocabulary made of it.

    Every derived field — column, operator, unit, axis, the note — comes from the
    vocabulary rather than from the caller, and the values are served as recorded:
    the outflow entry relabels litres as millimetres and scales nothing
    (``agent_architecture.md`` §3.6).
    """
    entry = prepared.variable
    assert isinstance(entry, MeasuredVariable)  # noqa: S101 - `prepare_series` decides this
    column = str(prepared.column)
    result = executor.execute_query(_measured_query(entry, column, start, end, resolution))
    return _resolved_series(
        prepared,
        [(_stamp(at), float(value)) for at, value in result.rows],
        start,
        end,
        table=entry.table,
        column=column,
    )


def resolve_measured_series(
    executor: ReadOnlyWarehouseQuery,
    spec: SeriesSpec,
    start: str,
    end: str,
    resolution: Resolution,
) -> ResolvedSeries:
    """Prepare and read one ``measured`` series, for a caller holding a bare spec.

    Raises:
        PlotVocabularyError: the series is outside the closed vocabulary.
    """
    return fetch_measured_series(
        executor, prepare_series(spec, start, end), start, end, resolution
    )


def weather_series(
    weather: WeatherResult, prepared: PreparedSeries, start: str, end: str
) -> ResolvedSeries:
    """One ``weather`` series off the rows ``ctx.weather`` returned.

    Already daily and already unit-converted, so nothing is aggregated here: the
    row *is* the point, under the field name the agent named and the other two
    tools report. Which source served is echoed rather than chosen — an answer
    drawn from the site's own instruments says so (§3.3).
    """
    field = prepared.spec.variable
    return _resolved_series(
        prepared,
        [
            (row.Date, float(value))
            for row in weather.data
            if (value := getattr(row, field, None)) is not None
        ],
        start,
        end,
        weather_source=weather.source,
    )


def model_series(
    run: ModelRun, prepared: PreparedSeries, start: str, end: str
) -> ResolvedSeries:
    """One ``model`` series off a GR2L run, with the run's disclosures beside it.

    A null value is dropped rather than drawn as a zero — the seed day computes
    no runoff — and reappears in ``gaps``, which is what the day the chart is
    missing should look like from the model's side too.
    """
    field = prepared.spec.variable
    spec = prepared.spec
    return _resolved_series(
        prepared,
        [
            (day.Date, float(value))
            for day in run.days
            if (value := getattr(day, field, None)) is not None
        ],
        start,
        end,
        weather_source=run.weather_source,
        seed=run.seed,
        initial_soil_moisture_pct=spec.initial_soil_moisture_pct,
        albedo=spec.albedo,
        forcings=run.forcings,
    )


def _stamp(at: date | datetime) -> str:
    """One point's label: an ISO day when the plot is daily, an instant otherwise.

    The daily branch groups to a ``DATE`` and the half-hourly branch selects the
    site's wall clock, so the two arrive as different types and are written as
    the different things they are — a day a total belongs to, or the moment a
    sample was taken.
    """
    return at.isoformat(sep=" ") if isinstance(at, datetime) else at.isoformat()


def _parse_specs(series: Any) -> list[SeriesSpec]:
    """Validate the ``series`` argument into specs.

    Raises:
        PlotVocabularyError: the argument is not a non-empty list of series
            declarations, or one of them carries a field this tool does not know.
    """
    if not isinstance(series, list) or not series:
        raise PlotVocabularyError(
            "series must be a non-empty list of series declarations, e.g. "
            '[{"source": "measured", "table": "swc", "variable": "soil_moisture", '
            '"roof": "irrigated_extensive"}].'
        )
    specs: list[SeriesSpec] = []
    for index, item in enumerate(series):
        try:
            specs.append(SeriesSpec.model_validate(item))
        except ValidationError as exc:
            faults = "; ".join(
                f"{'.'.join(str(part) for part in error['loc']) or 'series'}: {error['msg']}"
                for error in exc.errors()
            )
            raise PlotVocabularyError(f"series[{index}] is not a valid declaration — {faults}.") from exc
    return specs


class _PlotSources:
    """One plot's live fetches, each done once.

    A chart draws the same window from at most one weather fetch and one run per
    roof-and-counterfactual: two model series over one roof — its soil moisture
    against its runoff — are one simulation, not two, and asking GR2L twice for
    the same request would double a case's captures for no second answer.
    Re-fetching *across* calls stays cheap for the reason §3.6 gives (both live
    sources replay from the response cache); this is only about within one chart.
    """

    def __init__(self, ctx: "ScenarioContext", start: str, end: str) -> None:
        self._ctx = ctx
        self._start = start
        self._end = end
        self._weather: WeatherResult | None = None
        self._runs: dict[str, ModelRun] = {}

    async def weather(self) -> WeatherResult:
        """The window's daily weather, from whichever source covers it."""
        if self._weather is None:
            self._weather = await self._ctx.weather.fetch(
                start_date=self._start, end_date=self._end
            )
        return self._weather

    async def model(self, prepared: PreparedSeries) -> ModelRun:
        """The GR2L run this series reads, keyed by everything that could change it."""
        spec = prepared.spec
        key = json.dumps(
            [
                prepared.roof.name if prepared.roof is not None else None,
                spec.initial_soil_moisture_pct,
                spec.albedo,
                prepared.forcings,
            ],
            sort_keys=True,
        )
        if key not in self._runs:
            self._runs[key] = await run_roof_model(
                self._ctx,
                str(prepared.roof.name) if prepared.roof is not None else "",
                self._start,
                self._end,
                initial_soil_moisture_pct=spec.initial_soil_moisture_pct,
                albedo=spec.albedo,
                forcings=prepared.forcings,
            )
        return self._runs[key]

    async def fetch(self, prepared: PreparedSeries, resolution: Resolution) -> ResolvedSeries:
        """Draw one prepared series from whichever source it declared."""
        if prepared.spec.source == "measured":
            return await asyncio.to_thread(
                fetch_measured_series,
                self._ctx.db,
                prepared,
                self._start,
                self._end,
                resolution,
            )
        if prepared.spec.source == "weather":
            return weather_series(await self.weather(), prepared, self._start, self._end)
        return model_series(await self.model(prepared), prepared, self._start, self._end)


def make_plot_timeseries_tool(ctx: "ScenarioContext") -> PlotTimeseriesTool:
    """Build ``plot_timeseries`` bound to *ctx*.

    Four bindings, all read per call: ``ctx.as_of`` resolves the window, ``ctx.db``
    — the case's as-of executor — serves every ``measured`` series, ``ctx.weather``
    serves the ``weather`` ones, and ``ctx.cache`` is what
    :func:`~.gr2l.run_roof_model` routes GR2L through for the ``model`` ones. That
    is the whole of the fetch surface: this tool opens no DuckDB connection and no
    HTTP client of its own, which is the condition §3.6 makes its
    self-containment conditional on (``agent_architecture.md`` §3.6, §4).
    """

    async def plot_timeseries(
        series: list[dict[str, Any]],
        start_date: str | None = None,
        end_date: str | None = None,
        past_days: int | None = None,
        forecast_days: int | None = None,
        kind: str = "line",
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Draw a chart of one or more time series over a date window.

        **Fetches its own data; do not query the database separately for
        plotting**, and do not pass values in — each series names a *source* and a
        *variable*, and the tool reads it. Use it when the user asks to see, show,
        plot or chart how something developed over a period.

        A series is drawn from one of three sources, and they can share a chart:

        **``measured``** — the site's own record, through a fixed vocabulary of
        variables per table. Give ``table`` and ``variable``, plus ``roof`` where
        the table is per roof:

        - ``swc`` — ``soil_moisture`` (%θ), per roof
        - ``tsoil`` — ``soil_temperature`` (°C), per roof
        - ``outflow`` — ``outflow`` (mm of runoff), per roof, lysimeter roofs only
        - ``wetter`` — the site's weather station, no roof: ``precipitation`` (mm),
          ``air_temperature`` (°C), ``relative_humidity`` (%),
          ``shortwave_radiation`` (W/m²)
        - ``radiation`` — the roofs' radiation masts: ``shortwave_down``,
          ``shortwave_up``, ``longwave_down``, ``longwave_up`` (W/m²),
          ``surface_temperature``, ``surface_temperature_corrected`` (K)

        **``weather``** — the daily weather for the facility, the same rows the
        weather tool reports and under the same short names, no roof and no
        table: ``precip`` (mm), ``tm`` / ``tx`` / ``tn`` (°C), ``rf`` (%), ``w``
        (km/h, not m/s), ``gs`` (J/cm²/day, not W/m²).

        **``model``** — a GR2L simulation of one ``roof``: ``swc_pct`` (%θ),
        ``Ssub`` / ``Sret`` (mm of stored water), ``OUT`` (mm of runoff), ``ET`` /
        ``ET_PM`` (mm of actual / potential evapotranspiration), ``Qdown`` /
        ``Qup`` (mm). The tool fetches the weather and reads the roof's own
        starting soil moisture itself — do not call another tool first.

        Nothing else can be drawn: a variable outside these lists comes back as an
        ``invalid_argument`` naming what was valid, and there is no way to plot a
        computed column. For a quantity they do not carry, query the database
        instead and answer from the numbers.

        How the series is aggregated is **not** a choice: rain and runoff sum over
        a day, water contents and temperatures average, and the result says which
        operator ran. Half-hourly detail is kept for an all-measured plot and
        aggregated to calendar days (Europe/Berlin) as soon as a daily series
        shares the chart.

        Args:
            series: The series to draw, as a list of declarations. Each takes
                ``source`` (``"measured"``, ``"weather"`` or ``"model"``),
                ``variable``, and — for a ``measured`` series over a per-roof
                table or for any ``model`` series — ``roof``, in whatever spelling
                the user used: ``"Kiesdach"`` and ``"gravel"`` both resolve. A
                ``measured`` series also takes ``table``. Two roofs compared over
                one quantity are two series with the same ``variable`` and
                different ``roof``; a measurement against its prediction is a
                ``measured`` and a ``model`` series side by side. A ``model``
                series may also carry ``initial_soil_moisture_pct`` (%θ),
                ``albedo`` (0.0-1.0) and ``forcings`` (what-if weather,
                ``{"precip": {"2026-07-22": 50.0}}``) — omit all three in normal
                use, and say in the answer whenever one was set.
            start_date: Window start, ``YYYY-MM-DD`` (give ``end_date`` with it).
            end_date: Window end, ``YYYY-MM-DD``. Required whenever ``start_date``
                is given.
            past_days: Number of **complete past days**, ending yesterday.
            forecast_days: Number of days from **today** forward.
            kind: ``line`` (the default), ``bar`` for daily totals such as rain,
                ``model_overlay`` for a measurement drawn against a prediction, or
                ``diff`` for the gap between two series.

        Returns:
            dict: on success ``status='success'`` with the resolved ``start`` and
            ``end``, the ``resolution`` the series were drawn at, and one entry
            per series carrying what was asked for (source, variable, roof, any modelling arguments)
            and what followed from it (the ``column`` read, the ``aggregation``
            applied, the ``unit`` and the ``axis``, and ``stats`` — how many points
            were drawn, over what span, and their min, max, mean and total).
            **The values themselves are not returned** — the chart is the
            deliverable and the user can already see it, so describe what was drawn
            rather than reciting numbers.

            Four things in a series are caveats to pass on rather than details to
            drop: a ``note`` (the radiation masts' one-hour timestamp offset,
            outflow's litres-are-millimetres relabel, the modelled seed day that
            computes no runoff), ``gaps`` above zero (days the series has no point
            for), ``truncated`` (the series stops before the window does, because
            the record ends there), and a ``seed`` whose ``is_stale`` is true (the
            modelled run started from an old sensor reading). ``weather_source``
            of ``'station'`` means the site's own instruments — say so.

            When the window reaches past the forecast horizon,
            ``status='not_available'`` with a ``reason`` to pass on: that is a
            scope limit, not a malfunction, and the measured record can still be
            drawn. On failure
            ``status='error'`` with ``error_details`` and an ``error_type``:
            ``'invalid_argument'`` means the call itself was wrong and can be
            corrected and retried, ``'upstream'`` means something the tool depends
            on failed.
        """
        if kind not in PLOT_KINDS:
            return ErrorResult(
                error_type="invalid_argument",
                error_details=f"Unknown kind {kind!r}. Valid kinds: {', '.join(PLOT_KINDS)}.",
            ).model_dump()

        try:
            specs = _parse_specs(series)
        except PlotVocabularyError as exc:
            logger.info("Rejected a plot series declaration", error=str(exc))
            return ErrorResult(error_type="invalid_argument", error_details=str(exc)).model_dump()

        try:
            start, end = resolve_window(
                start_date, end_date, past_days, forecast_days, today=ctx.as_of.date()
            )
        except InvalidWindowError as exc:
            logger.info("Rejected a plot window", error=str(exc))
            return ErrorResult(error_type="invalid_argument", error_details=str(exc)).model_dump()

        # Decided over the whole plot before any series is fetched (T094): a
        # measured series sharing a chart with a daily one is aggregated to days.
        resolution = plot_resolution([spec.source for spec in specs])

        # Every argument fault and the one scope limit are settled here, before
        # the first fetch: a plot that cannot be drawn issues no query, no weather
        # fetch and — the reason it matters — no GR2L request, so a declined chart
        # never records a cache entry for a run nobody will read.
        try:
            prepared = [prepare_series(spec, start, end) for spec in specs]
        except (PlotVocabularyError, ForcingError) as exc:
            logger.info("Rejected a plot series", error=str(exc))
            return ErrorResult(
                error_type="invalid_argument", error_details=str(exc)
            ).model_dump()

        # The horizon is the live sources' limit, so it binds exactly the plots
        # that have one: an all-measured chart of a future window is simply empty,
        # while a modelled or forecast one would be a chart of weather that does
        # not exist (§3.3, §3.4 — the same check, on the same resolved window).
        today = ctx.as_of.date()
        if any(item.spec.source != "measured" for item in prepared) and beyond_horizon(
            end, today
        ):
            logger.info("A plot's live sources reach past the forecast horizon", window=(start, end))
            return NotAvailableResult(
                reason=(
                    f"Weather and modelled series are only available up to "
                    f"{FORECAST_HORIZON_DAYS} days ahead (through "
                    f"{today + timedelta(days=FORECAST_HORIZON_DAYS):%Y-%m-%d}), and the "
                    f"window asked for ends {end}. The measured record can still be drawn."
                )
            ).model_dump()

        sources = _PlotSources(ctx, start, end)
        resolved: list[ResolvedSeries] = []
        for item in prepared:
            try:
                resolved.append(await sources.fetch(item, resolution))
            except SwcUnavailableError as exc:
                # No trustworthy seed is a scope limit, not a fault — the same one
                # the water-balance tool reports, never a made-up starting state.
                logger.info("A plot's modelled series has no seed", error=str(exc))
                return NotAvailableResult(reason=str(exc)).model_dump()
            except (WeatherFetchError, ModelWeatherError, Gr2lConfigError) as exc:
                logger.warning("A plot's live source failed", error=str(exc))
                return ErrorResult(error_type="upstream", error_details=str(exc)).model_dump()
            except Exception:
                logger.exception(
                    "Failed to read a series for a plot", source=item.spec.source
                )
                return ErrorResult(
                    error_type="upstream",
                    error_details=(
                        f"Failed to read the {item.spec.source} series "
                        f"{item.spec.variable!r} over {start}..{end}."
                    ),
                ).model_dump()

        logger.debug(
            "Resolved a plot",
            window=(start, end),
            resolution=resolution,
            series=[item.spec.variable for item in resolved],
            points=[len(item.points) for item in resolved],
        )
        # The points stay out of the payload: the model gets the spec and the
        # statistics, and the renderer gets the series through the state handoff.
        return PlotResult(
            kind=kind,
            start=start,
            end=end,
            resolution=resolution,
            series=[item.spec for item in resolved],
        ).model_dump()

    # ADK reads `__name__`; the qualname is reset so a `<locals>`-qualified name
    # never surfaces in logs or reprs (as in ``gr2l.make_green_roof_balance_tool``).
    plot_timeseries.__qualname__ = "plot_timeseries"
    return plot_timeseries
