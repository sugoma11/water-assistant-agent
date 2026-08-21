"""ADK tool: time series drawn from declared sources, over a closed vocabulary.

The agent names **where** a series comes from and **which quantity** to draw;
this module fetches it through the seams the other tools already use and returns
the resolved spec. Nothing about the data itself is an argument, and nothing
about the data comes back to the model: the series' consumer is the renderer
(``agent_architecture.md`` §3.6, ``decisions.md`` § Plotting).

**The `measured` vocabulary is a table, and it is closed.** Twelve variables over
the five as-of tables, each carrying its column and whether it is a flux or a
state, which is what the operator follows from. A variable that is
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
millimetre of depth: the entry reports millimetres and the values are served
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
"""

import asyncio
import dataclasses
from collections.abc import Awaitable, Callable, Sequence
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Literal

import structlog
from google.adk.tools.tool_context import ToolContext
from pydantic import ValidationError

from water_assistant_agent.assistant.context import AS_OF_TABLES
from water_assistant_agent.assistant.ports import ReadOnlyWarehouseQuery
from water_assistant_agent.assistant.tools.roofs import (
    ROOFS,
    RoofSegment,
    radiation_column,
    resolve_roof,
    roofs_with_column,
)
from water_assistant_agent.assistant.tools.schemas import (
    ErrorResult,
    PlotResult,
    PlotSeries,
    SeriesSpec,
)
from water_assistant_agent.assistant.tools.site import site_day_expr, site_timestamp_expr
from water_assistant_agent.assistant.tools.weather_client import (
    InvalidWindowError,
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

    station_column: str | None = None
    """The ``wetter`` column, named here because the station belongs to no roof."""

    mast_suffix: str | None = None
    """The ``radiation`` mast suffix; the prefix is the roof's own (:mod:`roofs`)."""

    note: str | None = None
    """What a reader of this series has to be told — carried out with the spec."""

    @property
    def aggregation(self) -> Literal["sum", "mean"]:
        """The operator this variable is aggregated by. Derived, never chosen."""
        return "sum" if self.quantity == "flux" else "mean"

    @property
    def per_roof(self) -> bool:
        """True when the series is one roof's, so a ``roof`` selector is required."""
        return self.station_column is None


MEASURED_VOCABULARY: dict[tuple[str, str], MeasuredVariable] = {
    # Per-roof tables: no column here, because `roofs.py` has it.
    ("swc", "soil_moisture"): MeasuredVariable(
        table="swc", quantity="state"
    ),
    ("tsoil", "soil_temperature"): MeasuredVariable(
        table="tsoil", quantity="state"
    ),
    ("outflow", "outflow"): MeasuredVariable(
        table="outflow",
        quantity="flux",
        note=_OUTFLOW_NOTE,
    ),
    # The station: one set of columns for the whole site, so these name theirs.
    ("wetter", "precipitation"): MeasuredVariable(
        table="wetter",
        quantity="flux",
        station_column="Rain",
    ),
    ("wetter", "air_temperature"): MeasuredVariable(
        table="wetter",
        quantity="state",
        station_column="Tmean",
    ),
    ("wetter", "relative_humidity"): MeasuredVariable(
        table="wetter",
        quantity="state",
        station_column="RH",
    ),
    ("wetter", "shortwave_radiation"): MeasuredVariable(
        table="wetter",
        quantity="state",
        station_column="Rad_SW",
    ),
    # The masts: a suffix each, joined to the roof's prefix by `roofs.py`.
    ("radiation", "shortwave_down"): MeasuredVariable(
        table="radiation",
        quantity="state",
        mast_suffix="SWdown",
        note=_RADIATION_NOTE,
    ),
    ("radiation", "shortwave_up"): MeasuredVariable(
        table="radiation",
        quantity="state",
        mast_suffix="SWup",
        note=_RADIATION_NOTE,
    ),
    ("radiation", "longwave_down"): MeasuredVariable(
        table="radiation",
        quantity="state",
        mast_suffix="LWdown",
        note=_RADIATION_NOTE,
    ),
    ("radiation", "longwave_up"): MeasuredVariable(
        table="radiation",
        quantity="state",
        mast_suffix="LWup",
        note=_RADIATION_NOTE,
    ),
    ("radiation", "surface_temperature"): MeasuredVariable(
        table="radiation",
        quantity="state",
        mast_suffix="TSFC",
        note=_RADIATION_NOTE,
    ),
    ("radiation", "surface_temperature_corrected"): MeasuredVariable(
        table="radiation",
        quantity="state",
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


def resolve_measured_series(
    executor: ReadOnlyWarehouseQuery,
    spec: SeriesSpec,
    start: str,
    end: str,
    resolution: Resolution,
) -> ResolvedSeries:
    """Fetch one ``measured`` series and echo what the vocabulary made of it.

    Every derived field — the column, the operator, the note — comes from the
    vocabulary rather than from the caller, and the values are served as recorded:
    the outflow entry reports the record's litres as millimetres and scales
    nothing (``agent_architecture.md`` §3.6).

    Raises:
        PlotVocabularyError: the series is outside the closed vocabulary.
    """
    entry = measured_variable(spec.table, spec.variable)
    column, segment = measured_column(entry, spec.roof)
    result = executor.execute_query(_measured_query(entry, column, start, end, resolution))
    return ResolvedSeries(
        spec=PlotSeries(
            source="measured",
            variable=spec.variable,
            table=entry.table,
            roof=segment.name if segment is not None else None,
            column=column,
            quantity=entry.quantity,
            aggregation=entry.aggregation,
            note=entry.note,
        ),
        points=[(_stamp(at), float(value)) for at, value in result.rows],
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


def make_plot_timeseries_tool(ctx: "ScenarioContext") -> PlotTimeseriesTool:
    """Build ``plot_timeseries`` bound to *ctx*.

    Two bindings today, both read per call: ``ctx.as_of`` resolves the window and
    ``ctx.db`` — the case's as-of executor — serves every ``measured`` series. The
    two live sources bind ``ctx.weather`` and ``run_gr2l`` when they land (T091),
    which is why this tool opens no DuckDB connection and no HTTP client of its
    own: three sources, one context, no second fetch path
    (``agent_architecture.md`` §3.6, §4).
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

        Measured series are the site's own record, through a fixed vocabulary of
        variables per table:

        - ``swc`` — ``soil_moisture`` (%θ), per roof
        - ``tsoil`` — ``soil_temperature`` (°C), per roof
        - ``outflow`` — ``outflow`` (mm of runoff), per roof, lysimeter roofs only
        - ``wetter`` — the site's weather station, no roof: ``precipitation`` (mm),
          ``air_temperature`` (°C), ``relative_humidity`` (%),
          ``shortwave_radiation`` (W/m²)
        - ``radiation`` — the roofs' radiation masts: ``shortwave_down``,
          ``shortwave_up``, ``longwave_down``, ``longwave_up`` (W/m²),
          ``surface_temperature``, ``surface_temperature_corrected`` (K)

        Nothing else can be drawn: a variable outside this list comes back as an
        ``invalid_argument`` naming what was valid, and there is no way to plot a
        computed column. For a quantity this list does not carry, query the
        database instead and answer from the numbers.

        How the series is aggregated is **not** a choice: rain and runoff sum over
        a day, water contents and temperatures average, and the result says which
        operator ran. Half-hourly detail is kept for an all-measured plot and
        aggregated to calendar days (Europe/Berlin) as soon as a daily series
        shares the chart.

        Args:
            series: The series to draw, as a list of declarations. Each takes
                ``source`` (``"measured"``), ``variable`` and, on a per-roof
                table, ``roof`` in whatever spelling the user used — ``"Kiesdach"``
                and ``"gravel"`` both resolve. A ``measured`` series also takes
                ``table``. Two roofs compared over one quantity are two series
                with the same ``variable`` and different ``roof``.
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
            ``end``, the ``resolution`` the series were drawn at, and one entry per
            series carrying what was asked for (source, variable, roof) and what
            followed from it — the ``column`` read and the ``aggregation`` applied.
            **The values themselves are not returned** —
            the chart is the deliverable and the user can already see it, so
            describe what was drawn rather than reciting numbers. A series carrying
            a ``note`` — the radiation masts' one-hour timestamp offset, outflow's
            litres-are-millimetres relabel — is a series whose caveat belongs in the
            answer. On failure ``status='error'`` with ``error_details`` and an
            ``error_type``: ``'invalid_argument'`` means the call itself was wrong
            and can be corrected and retried, ``'upstream'`` means something the
            tool depends on failed.
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

        resolved: list[ResolvedSeries] = []
        for spec in specs:
            if spec.source != "measured":
                # `weather` and `model` resolve through `ctx.weather` and `run_gr2l`
                # — the same clients, cache, window resolution and seed rule the
                # standalone tools use — and that wiring is T091's. Until it lands
                # this tool is not in `build_toolset`, so no agent can reach here;
                # what may never happen is a second fetch path growing in the
                # meantime to make the branch work.
                raise NotImplementedError(
                    f"A {spec.source!r} series resolves through the shared clients (T091)."
                )
            try:
                resolved.append(
                    await asyncio.to_thread(
                        resolve_measured_series, ctx.db, spec, start, end, resolution
                    )
                )
            except PlotVocabularyError as exc:
                logger.info("Rejected a plot series", error=str(exc))
                return ErrorResult(
                    error_type="invalid_argument", error_details=str(exc)
                ).model_dump()
            except Exception:
                logger.exception("Failed to read a measured series for a plot")
                return ErrorResult(
                    error_type="upstream",
                    error_details=(
                        f"Failed to read {spec.variable!r} from the database over "
                        f"{start}..{end}."
                    ),
                ).model_dump()

        logger.debug(
            "Resolved a plot",
            window=(start, end),
            resolution=resolution,
            series=[item.spec.variable for item in resolved],
            points=[len(item.points) for item in resolved],
        )
        # The points stay out of the payload: the model gets the spec, and the
        # renderer gets the series through the session-state handoff (T097).
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
