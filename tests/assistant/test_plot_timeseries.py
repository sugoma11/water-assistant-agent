"""The plot tool's measured half: the closed vocabulary and the derived operators.

Covers T090, T094 and T095 — the vocabulary as a data table over the five as-of
tables, the operator/unit/axis derivation, and the forced Europe/Berlin day —
plus the measured share of T099. The two live sources are T091's, and nothing
here stands in for them: what is asserted about a mixed plot is asserted about
the *decision* it forces, which is made from the sources alone before anything is
fetched.

Two conventions, both borrowed from ``test_gr2l_tool.py`` for its reasons. The
assertions run against the real pinned ``data/water.duckdb``, because the
aggregation under test *is* that file and a fixture would pin a grouping over
rows this deployment never serves. And every expected number is hand-computed in
Python from the raw rows — ``zoneinfo`` for the day boundary, ``statistics`` for
the mean — never by running a second copy of the implementation's own query,
which would let the boundary drift in both places at once and still pass.
"""

from __future__ import annotations

import asyncio
import inspect
import statistics
from collections import defaultdict
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import pytest

from water_assistant_agent.assistant.context import AS_OF_TABLES, ScenarioContext
from water_assistant_agent.assistant.ports import QueryResult
from water_assistant_agent.assistant.tools.plot import (
    MEASURED_TABLES,
    MEASURED_VOCABULARY,
    PlotVocabularyError,
    make_plot_timeseries_tool,
    measured_column,
    measured_variable,
    plot_resolution,
    resolve_measured_series,
)
from water_assistant_agent.assistant.tools.roofs import (
    LYSIMETER_AREA_M2,
    ROOFS,
    radiation_column,
    roofs_with_column,
)
from water_assistant_agent.assistant.tools.schemas import SeriesSpec

DB_PATH = "data/water.duckdb"
BERLIN = ZoneInfo("Europe/Berlin")

# Past every record's end, so a retrospective window sees all of it.
AFTER_RECORD = datetime(2026, 5, 1, 0, 0, tzinfo=BERLIN)

# A week inside every table's cover except `radiation`'s, whose own window is
# 2025-03-01 to 2025-10-01 — this sits inside that too.
WINDOW = ("2025-07-01", "2025-07-07")


def _context(as_of: datetime = AFTER_RECORD) -> ScenarioContext:
    """A context frozen at *as_of*; the weather half is T091's scope, not this file's."""
    return ScenarioContext(
        clock=lambda: as_of,
        db_path=DB_PATH,
        weather_client_factory=lambda db, cache: None,
        http_cache=None,
    )


class SpyExecutor:
    """A ``ReadOnlyWarehouseQuery`` that records the SQL it is handed, if any."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    def execute_query(self, query: str) -> QueryResult:
        self.queries.append(query)
        return QueryResult(columns=("at", "value"), rows=[])


def _plot(tool: Any, **kwargs: Any) -> dict[str, Any]:
    start, end = WINDOW
    kwargs.setdefault("start_date", start)
    kwargs.setdefault("end_date", end)
    return asyncio.run(tool(**kwargs))


def _raw_rows(table: str, column: str) -> list[tuple[datetime, float]]:
    """Every non-null reading of *column*, straight from the pinned file."""
    connection = duckdb.connect(DB_PATH, read_only=True)
    try:
        return connection.execute(
            f'SELECT timestamp, "{column}" FROM {table} WHERE "{column}" IS NOT NULL ORDER BY 1'
        ).fetchall()
    finally:
        connection.close()


def _by_site_day(
    rows: list[tuple[datetime, float]], start: str, end: str
) -> dict[date, list[float]]:
    """*rows* bucketed into Europe/Berlin calendar days inside ``[start, end]``.

    The timestamps are naive UTC, so the conversion is explicit here — this is
    the boundary the implementation is being held to, written a second,
    independent time.
    """
    window = (date.fromisoformat(start), date.fromisoformat(end))
    buckets: dict[date, list[float]] = defaultdict(list)
    for at, value in rows:
        day = at.replace(tzinfo=UTC).astimezone(BERLIN).date()
        if window[0] <= day <= window[1]:
            buckets[day].append(value)
    return buckets


def _by_utc_day(
    rows: list[tuple[datetime, float]], start: str, end: str
) -> dict[date, list[float]]:
    """The same bucketing done wrong — the grouping the day boundary rejects."""
    window = (date.fromisoformat(start), date.fromisoformat(end))
    buckets: dict[date, list[float]] = defaultdict(list)
    for at, value in rows:
        if window[0] <= at.date() <= window[1]:
            buckets[at.date()].append(value)
    return buckets


# --- The vocabulary as a data table -------------------------------------------


def test_the_vocabulary_reaches_the_five_as_of_tables_and_nothing_else() -> None:
    """Every table it names is bounded by an as-of view, and all five are named."""
    assert MEASURED_TABLES == AS_OF_TABLES
    assert {table for table, _ in MEASURED_VOCABULARY} == set(AS_OF_TABLES)


@pytest.mark.parametrize(
    ("table", "variable"),
    sorted(key for key in MEASURED_VOCABULARY if MEASURED_VOCABULARY[key].per_roof),
)
def test_no_entry_re_lists_a_column_roofs_py_already_carries(table: str, variable: str) -> None:
    """A per-roof column is read off the roof table, not written down twice.

    Held against :mod:`roofs` for every roof the table instruments, so a column
    copied into the vocabulary would have to be copied correctly five times and
    stay correct — which is the arrangement ``roofs.py`` exists to end.
    """
    entry = MEASURED_VOCABULARY[(table, variable)]
    for name in roofs_with_column(table):
        roof = ROOFS[name]
        expected = (
            radiation_column(roof, entry.mast_suffix)
            if entry.mast_suffix is not None
            else roof.columns[table]
        )
        assert measured_column(entry, name) == (expected, roof)


def test_a_roof_is_drawable_exactly_where_the_record_instruments_it() -> None:
    """The semi-intensive roof has no lysimeter and no mast, so it has no series there."""
    for table in ("swc", "tsoil"):
        entry = measured_variable(table, "soil_moisture" if table == "swc" else "soil_temperature")
        column, roof = measured_column(entry, "semi_intensive")
        assert (column, roof.name) == (ROOFS["semi_intensive"].columns[table], "semi_intensive")

    for table, variable in (("outflow", "outflow"), ("radiation", "shortwave_down")):
        with pytest.raises(PlotVocabularyError, match="not instrumented"):
            measured_column(measured_variable(table, variable), "semi_intensive")


def test_fluxes_sum_and_states_average() -> None:
    """The operator is a function of the quantity, and of nothing else."""
    for entry in MEASURED_VOCABULARY.values():
        assert entry.aggregation == ("sum" if entry.quantity == "flux" else "mean")

    # The two the specification names by hand, so the mapping is pinned by
    # example as well as by rule: rain accumulates, soil moisture is sampled.
    assert MEASURED_VOCABULARY[("wetter", "precipitation")].aggregation == "sum"
    assert MEASURED_VOCABULARY[("swc", "soil_moisture")].aggregation == "mean"


def test_every_radiation_entry_discloses_the_hour_offset() -> None:
    """§8 requires the disclosure wherever `radiation` is reachable — here included.

    The offset is a property of the table, so it rides on every one of its
    entries rather than on the one a reader might happen to draw.
    """
    radiation = [entry for entry in MEASURED_VOCABULARY.values() if entry.table == "radiation"]
    assert len(radiation) == 6
    for entry in radiation:
        assert entry.note is not None
        assert "one hour behind" in entry.note

    # And nowhere else: a note on every entry would train a reader to skip them.
    assert all(
        entry.note is None
        for key, entry in MEASURED_VOCABULARY.items()
        if key[0] not in {"radiation", "outflow"}
    )


def test_outflow_is_relabelled_to_millimetres_and_has_no_area_normalized_twin() -> None:
    """1 L = 1 mm at a 1 m² lysimeter, so the vocabulary renames and never scales."""
    assert LYSIMETER_AREA_M2 == 1.0
    outflow = [key for key in MEASURED_VOCABULARY if key[0] == "outflow"]
    assert outflow == [("outflow", "outflow")]

    entry = MEASURED_VOCABULARY[("outflow", "outflow")]
    assert entry.unit == "mm"
    assert entry.note is not None and "litres" in entry.note


def test_the_reported_outflow_is_the_litres_the_record_holds() -> None:
    """The relabel is a relabel: no area factor multiplies the values on the way out."""
    start, end = WINDOW
    resolved = resolve_measured_series(
        _context().db,
        SeriesSpec(source="measured", table="outflow", variable="outflow", roof="gravel"),
        start,
        end,
        "daily",
    )
    expected = {
        day: sum(values)
        for day, values in _by_site_day(_raw_rows("outflow", "Kies_Efflux"), start, end).items()
    }
    assert [at for at, _ in resolved.points] == [day.isoformat() for day in sorted(expected)]
    for at, value in resolved.points:
        assert value == pytest.approx(expected[date.fromisoformat(at)])


# --- Derivation: unit, axis, operator (T095) ----------------------------------


def test_the_signature_carries_no_agg_argument() -> None:
    """There is no operator to choose, so there is no argument to choose it with.

    One plot-level operator cannot serve a mixed plot — precipitation sums where
    soil moisture averages — so the field the model could fumble does not exist
    (``decisions.md`` § Plotting).
    """
    parameters = inspect.signature(make_plot_timeseries_tool(_context())).parameters
    assert "agg" not in parameters
    assert "aggregation" not in parameters
    assert "resolution" not in parameters
    assert "unit" not in parameters


def test_the_derived_fields_come_back_per_series() -> None:
    """Unit, axis and operator are echoed so a reader can see which one ran."""
    result = _plot(
        make_plot_timeseries_tool(_context()),
        series=[
            {"source": "measured", "table": "wetter", "variable": "precipitation"},
            {
                "source": "measured",
                "table": "outflow",
                "variable": "outflow",
                "roof": "irrigated_extensive",
            },
            {
                "source": "measured",
                "table": "swc",
                "variable": "soil_moisture",
                "roof": "irrigated_extensive",
            },
            {"source": "measured", "table": "wetter", "variable": "relative_humidity"},
        ],
    )
    assert result["status"] == "success"
    rain, outflow, moisture, humidity = result["series"]

    assert (rain["unit"], rain["aggregation"]) == ("mm", "sum")
    assert (outflow["unit"], outflow["aggregation"]) == ("mm", "sum")
    assert (moisture["unit"], moisture["aggregation"]) == ("%θ", "mean")

    # Rain and runoff are both depths and share a scale; two percentages of
    # different quantities do not, or a humidity of 70 % would set the scale a
    # soil moisture of 18 %θ is read against.
    assert rain["axis"] == outflow["axis"]
    assert moisture["axis"] != humidity["axis"]


def test_the_resolved_spec_echoes_the_column_the_roof_alias_resolved_to() -> None:
    """The agent may name a roof any way the site does; the answer names it one way."""
    result = _plot(
        make_plot_timeseries_tool(_context()),
        series=[{"source": "measured", "table": "swc", "variable": "soil_moisture", "roof": "Kies"}],
    )
    assert result["series"][0]["roof"] == "gravel"
    assert result["series"][0]["column"] == "QGravel"


# --- Resolution: the forced Europe/Berlin day (T094) --------------------------


def test_an_all_measured_plot_keeps_the_records_own_sampling() -> None:
    assert plot_resolution(["measured"]) == "half_hourly"
    assert plot_resolution(["measured", "measured"]) == "half_hourly"


@pytest.mark.parametrize(
    "sources",
    [
        ["measured", "model"],
        ["measured", "weather"],
        ["model", "measured"],
        ["measured", "measured", "weather"],
        ["weather", "model"],
    ],
)
def test_a_daily_source_forces_the_whole_plot_to_days(sources: list[str]) -> None:
    """`swc` and `wetter` are half-hourly while GR2L and the weather are daily."""
    assert plot_resolution(sources) == "daily"


def test_the_forced_days_are_the_sites_own_and_the_operator_is_the_variables() -> None:
    """A forced day is a Europe/Berlin calendar day, aggregated by the variable's own rule."""
    start, end = WINDOW
    executor = _context().db

    rain = resolve_measured_series(
        executor,
        SeriesSpec(source="measured", table="wetter", variable="precipitation"),
        start,
        end,
        "daily",
    )
    expected_rain = {
        day: sum(values)
        for day, values in _by_site_day(_raw_rows("wetter", "Rain"), start, end).items()
    }
    assert dict(_as_days(rain.points)) == pytest.approx(expected_rain)

    moisture = resolve_measured_series(
        executor,
        SeriesSpec(
            source="measured", table="swc", variable="soil_moisture", roof="irrigated_extensive"
        ),
        start,
        end,
        "daily",
    )
    expected_moisture = {
        day: statistics.fmean(values)
        for day, values in _by_site_day(_raw_rows("swc", "QEx1"), start, end).items()
    }
    assert dict(_as_days(moisture.points)) == pytest.approx(expected_moisture)


def _as_days(points: list[tuple[str, float]]) -> list[tuple[date, float]]:
    return [(date.fromisoformat(at), value) for at, value in points]


def test_utc_days_would_have_answered_differently() -> None:
    """The boundary is load-bearing on this window, not a formality.

    Two of the seven days' rain totals move when the grouping does, which is the
    scale at which a peak-day argmax flips (``findings.md``).
    """
    start, end = WINDOW
    rows = _raw_rows("wetter", "Rain")
    site = {day: sum(values) for day, values in _by_site_day(rows, start, end).items()}
    utc = {day: sum(values) for day, values in _by_utc_day(rows, start, end).items()}
    assert site != utc


def test_half_hourly_points_are_stamped_at_the_site() -> None:
    """A point's label and the day it is counted under agree, so neither reads as physics."""
    start, end = WINDOW
    resolved = resolve_measured_series(
        _context().db,
        SeriesSpec(source="measured", table="wetter", variable="precipitation"),
        start,
        end,
        "half_hourly",
    )
    stamps = [at for at, _ in resolved.points]
    assert stamps[0] == "2025-07-01 00:00:00"
    # Seven complete days of half-hours, and the first of them opens at 22:00 UTC
    # the day before — which is exactly the shift the stamp has to carry.
    assert len(stamps) == 7 * 48
    assert _raw_rows("wetter", "Rain")[0][0].minute in (0, 30)


# --- The as-of seam ------------------------------------------------------------


def test_a_plot_reads_through_the_cases_as_of_view() -> None:
    """The series stops at the case's cut, because it arrives through ``ctx.db``."""
    cut = datetime(2025, 7, 3, 12, 0, tzinfo=BERLIN)
    result_before = resolve_measured_series(
        _context(cut).db,
        SeriesSpec(source="measured", table="wetter", variable="precipitation"),
        *WINDOW,
        "half_hourly",
    )
    assert result_before.points
    assert max(at for at, _ in result_before.points) <= "2025-07-03 12:00:00"

    full = resolve_measured_series(
        _context().db,
        SeriesSpec(source="measured", table="wetter", variable="precipitation"),
        *WINDOW,
        "half_hourly",
    )
    assert len(full.points) > len(result_before.points)


# --- Closed-vocabulary rejection ----------------------------------------------


def test_an_unknown_table_is_rejected_and_names_the_five() -> None:
    result = _plot(
        make_plot_timeseries_tool(_context()),
        series=[{"source": "measured", "table": "sensors", "variable": "soil_moisture"}],
    )
    assert result["status"] == "error"
    assert result["error_type"] == "invalid_argument"
    assert "sensors" in result["error_details"]
    for table in AS_OF_TABLES:
        assert table in result["error_details"]


def test_an_unknown_variable_is_rejected_and_names_the_tables_own() -> None:
    result = _plot(
        make_plot_timeseries_tool(_context()),
        series=[
            {"source": "measured", "table": "swc", "variable": "runoff", "roof": "gravel"}
        ],
    )
    assert result["error_type"] == "invalid_argument"
    assert "soil_moisture" in result["error_details"]


def test_a_raw_column_name_is_not_a_variable() -> None:
    """The vocabulary is the closure: naming the database's own column is not a way past it."""
    result = _plot(
        make_plot_timeseries_tool(_context()),
        series=[{"source": "measured", "table": "swc", "variable": "QEx1", "roof": "gravel"}],
    )
    assert result["error_type"] == "invalid_argument"


def test_a_rejected_series_costs_no_query() -> None:
    """Vocabulary faults are argument faults, decided before any I/O."""
    spy = SpyExecutor()
    ctx = ScenarioContext.bound(clock=lambda: AFTER_RECORD, db=spy)
    result = _plot(
        make_plot_timeseries_tool(ctx),
        series=[
            {"source": "measured", "table": "swc", "variable": "soil_moisture", "roof": "gravel"},
            {"source": "measured", "table": "tsoil", "variable": "runoff", "roof": "gravel"},
        ],
    )
    assert result["error_type"] == "invalid_argument"
    # The first series is legal and was fetched; the second never reached SQL.
    assert len(spy.queries) == 1


@pytest.mark.parametrize(
    ("declaration", "fragment"),
    [
        ({"source": "measured", "table": "swc", "variable": "soil_moisture"}, "needs a roof"),
        (
            {"source": "measured", "table": "wetter", "variable": "precipitation", "roof": "gravel"},
            "takes no roof",
        ),
        (
            {
                "source": "measured",
                "table": "swc",
                "variable": "soil_moisture",
                "roof": "the north roof",
            },
            "Unknown roof",
        ),
        (
            {
                "source": "measured",
                "table": "outflow",
                "variable": "outflow",
                "roof": "semi_intensive",
            },
            "not instrumented",
        ),
        (
            {
                "source": "measured",
                "table": "swc",
                "variable": "soil_moisture",
                "roof": "gravel",
                "agg": "max",
            },
            "not a valid declaration",
        ),
        ({"source": "sensors", "variable": "soil_moisture"}, "not a valid declaration"),
    ],
)
def test_a_declaration_the_vocabulary_cannot_draw_is_an_argument_fault(
    declaration: dict[str, Any], fragment: str
) -> None:
    result = _plot(make_plot_timeseries_tool(_context()), series=[declaration])
    assert result["error_type"] == "invalid_argument"
    assert fragment in result["error_details"]


def test_an_empty_plot_and_an_unknown_kind_are_rejected() -> None:
    tool = make_plot_timeseries_tool(_context())
    assert _plot(tool, series=[])["error_type"] == "invalid_argument"

    result = _plot(
        tool,
        series=[{"source": "measured", "table": "wetter", "variable": "precipitation"}],
        kind="pie",
    )
    assert result["error_type"] == "invalid_argument"
    assert "model_overlay" in result["error_details"]


def test_a_plot_returns_no_series_values_to_the_model() -> None:
    """The chart is the deliverable; the numbers reach the renderer, not the answer."""
    result = _plot(
        make_plot_timeseries_tool(_context()),
        series=[{"source": "measured", "table": "wetter", "variable": "precipitation"}],
    )
    assert result["status"] == "success"
    assert "points" not in result["series"][0]
    assert "data" not in result
