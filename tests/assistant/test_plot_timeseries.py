"""The plot tool: three sources, one closed vocabulary, and no values to the model.

Covers T090–T097 and the whole of T099 — the vocabulary as a data table over the
five as-of tables, the operator/unit/axis derivation, the forced Europe/Berlin
day, the two live sources through the seams the other tools use, the gravel and
wetland abstention, the resolved-spec shape, and the packet's exit criterion: a
``model`` + ``weather`` + ``measured`` plot that issues no live call in replay.

Two conventions, both borrowed from ``test_gr2l_tool.py`` for its reasons. The
assertions run against the real pinned ``data/water.duckdb``, because the
aggregation under test *is* that file and a fixture would pin a grouping over
rows this deployment never serves. And every expected number is hand-computed in
Python from the raw rows — ``zoneinfo`` for the day boundary, ``statistics`` for
the mean — never by running a second copy of the implementation's own query,
which would let the boundary drift in both places at once and still pass.

The live half is asserted the same way ``test_gr2l_tool.py`` asserts it: each of
the two things outside this repository is replaced by something that **records
what it was asked**, so "the plot ran GR2L on the same rows the water-balance
tool would have" is read off the far side of the seam rather than inferred from
an answer that happens to agree.
"""

from __future__ import annotations

import asyncio
import inspect
import statistics
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import pytest

from water_assistant_agent.assistant.agents.root_agent.plot_tool import (
    PLOT_PAYLOAD_STATE_KEY,
    PlotTimeseriesTool,
    merge_plot_payload,
)
from water_assistant_agent.assistant.cache import ResponseCache
from water_assistant_agent.assistant.context import AS_OF_TABLES, ScenarioContext
from water_assistant_agent.assistant.ports import QueryResult
from water_assistant_agent.assistant.tools import gr2l as gr2l_module
from water_assistant_agent.assistant.tools import gr2l_client as gr2l_client_module
from water_assistant_agent.assistant.tools.gr2l_client import NON_MODELLABLE_ROOFS
from water_assistant_agent.assistant.tools.plot import (
    MEASURED_TABLES,
    MEASURED_VOCABULARY,
    MODEL_VOCABULARY,
    PLOT_SERIES_STATE_KEY,
    WEATHER_VOCABULARY,
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
from water_assistant_agent.assistant.tools.schemas import (
    DailyWeatherRow,
    GreenRoofDay,
    Gr2lResultRow,
    SeriesSpec,
    WeatherResult,
)
from water_assistant_agent.assistant.tools.weather_client import make_weather_client

DB_PATH = "data/water.duckdb"
BERLIN = ZoneInfo("Europe/Berlin")

# Past every record's end, so a retrospective window sees all of it.
AFTER_RECORD = datetime(2026, 5, 1, 0, 0, tzinfo=BERLIN)

# A week inside every table's cover except `radiation`'s, whose own window is
# 2025-03-01 to 2025-10-01 — this sits inside that too.
WINDOW = ("2025-07-01", "2025-07-07")

# The roof every modelled series here draws: instrumented in `swc`, so it has a
# seed, and inside `ROOF_PRESETS`, so GR2L has parameters for it.
MODELLED_ROOF = "irrigated_extensive"


class SpyWeather:
    """The weather half, recording every window it was asked for.

    Rows are flat and arbitrary — what these tests are about is which window was
    asked for and what the wrapper did with the answer, not the weather itself,
    which is ``test_weather_station``'s subject.
    """

    def __init__(self, source: str = "archive") -> None:
        self.calls: list[tuple[str, str]] = []
        self._source = source

    async def fetch(self, *, start_date: str, end_date: str) -> WeatherResult:
        self.calls.append((start_date, end_date))
        start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
        return WeatherResult(
            latitude=51.353484,
            longitude=12.432152,
            timezone="Europe/Berlin",
            source=self._source,
            data=[
                DailyWeatherRow(
                    Date=(start + timedelta(days=offset)).isoformat(),
                    tm=15.0 + offset,
                    tx=20.0,
                    tn=10.0,
                    rf=70.0,
                    precip=2.0,
                    w=5.0,
                    gs=1500.0,
                )
                for offset in range((end - start).days + 1)
            ],
        )


class SpyGr2l:
    """GR2L itself, recording the rows and parameters it was handed."""

    def __init__(self) -> None:
        self.rows: list[list[DailyWeatherRow]] = []
        self.parameters: list[Any] = []

    async def __call__(self, rows: list[DailyWeatherRow], parameters: Any, **kwargs: Any) -> Any:
        self.rows.append(list(rows))
        self.parameters.append(parameters)
        return [
            Gr2lResultRow(
                Date=row.Date,
                ET_PM=1.0,
                Ssub=10.0 + index,
                Sret=0.0,
                # Day 1 seeds the stores, so the fluxes it does not compute are
                # null — the shape the gap flag has to survive.
                Qdown=None if index == 0 else 0.0,
                Qup=None if index == 0 else 0.0,
                OUT=None if index == 0 else row.precip * 0.5,
                ET=0.8,
            )
            for index, row in enumerate(rows)
        ]


def _context(
    as_of: datetime = AFTER_RECORD,
    *,
    weather: Any = None,
    cache: ResponseCache | None = None,
    factory: Any = None,
) -> ScenarioContext:
    """A context frozen at *as_of*, with whichever live halves the test needs."""
    return ScenarioContext(
        clock=lambda: as_of,
        db_path=DB_PATH,
        weather_client_factory=factory or (lambda db, _cache: weather),
        http_cache=cache,
    )


def _gr2l(monkeypatch: pytest.MonkeyPatch) -> SpyGr2l:
    """Replace the one seam GR2L is called through, for both callers of it."""
    spy = SpyGr2l()
    monkeypatch.setattr(gr2l_module, "run_gr2l", spy)
    return spy


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


def test_a_rejected_plot_costs_no_query_at_all() -> None:
    """Argument faults are decided for the *whole plot* before any of it is fetched.

    Stronger than "the illegal series never reached SQL", and stronger for a
    reason the live sources introduced: the legal series here is first, so a
    per-series check would have drawn it before rejecting its neighbour. With a
    ``model`` series in that position, drawing it means a GR2L request — and a
    recorded cache entry — for a chart that then comes back an error and is never
    read. A plot is one deliverable, so it is one decision.
    """
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
    assert spy.queries == []


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


# --- The two live sources (T091) ----------------------------------------------

RAIN = {"source": "measured", "table": "wetter", "variable": "precipitation"}
FORECAST_RAIN = {"source": "weather", "variable": "precip"}
MODELLED_MOISTURE = {"source": "model", "variable": "swc_pct", "roof": MODELLED_ROOF}


def test_a_weather_series_is_ctx_weathers_own_row() -> None:
    """The daily row is the point: same field names, same source, nothing re-derived.

    The weather half is reached through ``ctx.weather`` — the composite that
    picks the station or the Archive from the window alone — so this tool names
    no provenance and opens no HTTP client of its own. It reports which source
    answered, exactly as the standalone weather tool does (§3.3).
    """
    weather = SpyWeather(source="archive")
    result = _plot(make_plot_timeseries_tool(_context(weather=weather)), series=[FORECAST_RAIN])

    assert result["status"] == "success"
    assert weather.calls == [WINDOW], "the resolved window, fetched once"

    series = result["series"][0]
    assert (series["source"], series["variable"]) == ("weather", "precip")
    assert (series["unit"], series["aggregation"]) == ("mm", "sum")
    assert series["weather_source"] == "archive"
    # Nothing a `measured` series carries: no table, no roof, no column.
    assert (series["table"], series["roof"], series["column"]) == (None, None, None)
    assert series["stats"]["points"] == 7


def test_a_model_series_is_a_gr2l_run_through_the_shared_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The model half runs GR2L on the rows ``ctx.weather`` returned, seeded from ``ctx.db``."""
    spy = _gr2l(monkeypatch)
    weather = SpyWeather(source="station")
    result = _plot(
        make_plot_timeseries_tool(_context(weather=weather)), series=[MODELLED_MOISTURE]
    )

    assert result["status"] == "success"
    assert weather.calls == [WINDOW]
    assert len(spy.rows) == 1
    assert [row.Date for row in spy.rows[0]] == list(_window_days(*WINDOW))

    series = result["series"][0]
    assert (series["source"], series["variable"], series["roof"]) == (
        "model",
        "swc_pct",
        MODELLED_ROOF,
    )
    assert (series["unit"], series["aggregation"]) == ("%θ", "mean")
    assert series["weather_source"] == "station"
    # The seed is the roof's own sensor reading, and it is the state GR2L was
    # actually sent — the echo and the request cannot disagree.
    assert series["seed"]["source"] == "measured"
    assert spy.parameters[0].theta_01 == series["seed"]["substrate_storage_mm"]
    assert series["stats"]["points"] == 7


def _window_days(start: str, end: str) -> list[str]:
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    return [(first + timedelta(days=n)).isoformat() for n in range((last - first).days + 1)]


def test_the_plots_model_run_is_the_standalone_tools_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same clients, cache, window resolution and seed rule — asserted as identity.

    Both callers reach GR2L through :func:`gr2l.run_gr2l`, so what each was about
    to send is recorded at that seam and the two requests are compared whole. It
    is the strongest form of "the same rule ran": a seed bound, a forcing overlay
    or an elevation that drifted on one side would move the rows or the
    parameters, and nothing else in this file would notice.
    """
    spy = _gr2l(monkeypatch)
    weather = SpyWeather(source="station")
    context = _context(weather=weather)

    standalone = asyncio.run(
        gr2l_module.make_green_roof_balance_tool(context)(
            MODELLED_ROOF, start_date=WINDOW[0], end_date=WINDOW[1]
        )
    )
    plotted = _plot(make_plot_timeseries_tool(context), series=[MODELLED_MOISTURE])

    assert standalone["status"] == "success"
    assert plotted["status"] == "success"
    assert len(spy.rows) == 2
    assert spy.rows[0] == spy.rows[1]
    assert spy.parameters[0] == spy.parameters[1]
    # And the plot's echoed seed is the tool's echoed seed, field for field.
    assert plotted["series"][0]["seed"] == standalone["seed"]


def test_one_roof_drawn_twice_is_one_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """A roof's moisture against its own runoff is one simulation, not two.

    Re-fetching across calls is cheap because both live sources replay from the
    response cache (§3.6); asking GR2L twice for the identical request inside one
    chart is not re-fetching, it is a second capture of one answer.
    """
    spy = _gr2l(monkeypatch)
    weather = SpyWeather()
    result = _plot(
        make_plot_timeseries_tool(_context(weather=weather)),
        series=[MODELLED_MOISTURE, {"source": "model", "variable": "OUT", "roof": MODELLED_ROOF}],
    )

    assert result["status"] == "success"
    assert len(spy.rows) == 1
    assert weather.calls == [WINDOW]


def test_two_series_over_one_window_share_one_weather_fetch() -> None:
    weather = SpyWeather()
    _plot(
        make_plot_timeseries_tool(_context(weather=weather)),
        series=[FORECAST_RAIN, {"source": "weather", "variable": "tx"}],
    )
    assert weather.calls == [WINDOW]


def test_a_counterfactual_is_applied_to_the_forcing_and_echoed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The what-if reaches the rows GR2L runs on, and the answer says what was assumed."""
    spy = _gr2l(monkeypatch)
    forced_day = WINDOW[0]
    result = _plot(
        make_plot_timeseries_tool(_context(weather=SpyWeather())),
        series=[
            {
                **MODELLED_MOISTURE,
                "albedo": 0.4,
                "initial_soil_moisture_pct": 12.5,
                "forcings": {"precip": {forced_day: 50.0}},
            }
        ],
    )

    assert result["status"] == "success"
    series = result["series"][0]
    # Echoed for argument checking: what the agent supplied, as it supplied it.
    assert series["albedo"] == 0.4
    assert series["initial_soil_moisture_pct"] == 12.5
    assert series["forcings"] == {"precip": {forced_day: 50.0}}

    # And applied where a counterfactual has to be applied — to the forcing.
    assert spy.rows[0][0].precip == 50.0
    assert spy.rows[0][1].precip == 2.0
    assert spy.parameters[0].albedo == 0.4
    # A stated seed is the caller's, not the sensor's, and it says so.
    assert series["seed"]["source"] == "caller"
    assert series["seed"]["swc_pct"] == 12.5


def test_a_modelling_argument_on_a_series_that_cannot_use_one_is_rejected() -> None:
    """`extra="forbid"` catches a field no series has; this catches a misplaced one."""
    result = _plot(
        make_plot_timeseries_tool(_context(weather=SpyWeather())),
        series=[{**RAIN, "albedo": 0.4}],
    )
    assert result["error_type"] == "invalid_argument"
    assert "albedo" in result["error_details"]


def test_a_weather_series_takes_no_roof_and_a_model_series_needs_one() -> None:
    tool = make_plot_timeseries_tool(_context(weather=SpyWeather()))

    weather_with_roof = _plot(tool, series=[{**FORECAST_RAIN, "roof": MODELLED_ROOF}])
    assert weather_with_roof["error_type"] == "invalid_argument"
    assert "takes no roof" in weather_with_roof["error_details"]

    model_without_roof = _plot(tool, series=[{"source": "model", "variable": "swc_pct"}])
    assert model_without_roof["error_type"] == "invalid_argument"
    assert "needs a roof" in model_without_roof["error_details"]


def test_an_unknown_variable_on_a_live_source_names_that_sources_own() -> None:
    tool = make_plot_timeseries_tool(_context(weather=SpyWeather()))

    weather = _plot(tool, series=[{"source": "weather", "variable": "precipitation"}])
    assert weather["error_type"] == "invalid_argument"
    assert "precip" in weather["error_details"]

    model = _plot(tool, series=[{"source": "model", "variable": "runoff", "roof": MODELLED_ROOF}])
    assert model["error_type"] == "invalid_argument"
    assert "OUT" in model["error_details"]


def test_a_forcing_outside_the_window_costs_no_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """An argument fault in the counterfactual is settled before the first hop."""
    spy = _gr2l(monkeypatch)
    weather = SpyWeather()
    result = _plot(
        make_plot_timeseries_tool(_context(weather=weather)),
        series=[{**MODELLED_MOISTURE, "forcings": {"precip": {"2025-08-01": 50.0}}}],
    )

    assert result["error_type"] == "invalid_argument"
    assert "outside the window" in result["error_details"]
    assert weather.calls == []
    assert spy.rows == []


# --- The vocabularies the live sources draw from ------------------------------


def test_the_weather_vocabulary_is_the_daily_rows_own_fields() -> None:
    """One vocabulary across the three tools: a plot draws `tx`, a forcing forces `tx`."""
    assert set(WEATHER_VOCABULARY) == set(DailyWeatherRow.model_fields) - {"Date"}
    assert set(WEATHER_VOCABULARY) == set(gr2l_module.FORCEABLE_FIELDS)


def test_the_model_vocabulary_is_the_modelled_days_own_fields() -> None:
    assert set(MODEL_VOCABULARY) == set(GreenRoofDay.model_fields) - {"Date"}


def test_a_modelled_quantity_shares_the_axis_of_the_thing_it_models() -> None:
    """What makes an overlay a comparison rather than two charts in one frame."""
    assert (
        MODEL_VOCABULARY["swc_pct"].axis == MEASURED_VOCABULARY[("swc", "soil_moisture")].axis
    )
    assert MODEL_VOCABULARY["OUT"].axis == MEASURED_VOCABULARY[("outflow", "outflow")].axis
    # And the two that share a unit without sharing a quantity do not share a
    # scale: what the roof holds is not what ran off it.
    assert MODEL_VOCABULARY["Ssub"].unit == MODEL_VOCABULARY["OUT"].unit
    assert MODEL_VOCABULARY["Ssub"].axis != MODEL_VOCABULARY["OUT"].axis
    # Nor does a day's accumulated radiation share the instantaneous axis.
    assert (
        WEATHER_VOCABULARY["gs"].axis
        != MEASURED_VOCABULARY[("wetter", "shortwave_radiation")].axis
    )


def test_every_live_variable_derives_its_operator_from_its_quantity() -> None:
    for entry in (*WEATHER_VOCABULARY.values(), *MODEL_VOCABULARY.values()):
        assert entry.aggregation == ("sum" if entry.quantity == "flux" else "mean")
    assert WEATHER_VOCABULARY["precip"].aggregation == "sum"
    assert MODEL_VOCABULARY["swc_pct"].aggregation == "mean"


# --- Typed outcomes: the two roofs GR2L has no store for (T096) ----------------


@pytest.mark.parametrize("roof", ["gravel", "Kiesdach", "wetland", "Sumpfdach"])
def test_a_modelled_gravel_or_wetland_series_is_not_available(
    roof: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing has failed: the model has no store for these two, and says so.

    In the tool's own words — the reason comes from ``NON_MODELLABLE_ROOFS``,
    which both water-balance tools are bounded by — so the plot declines for the
    same stated cause rather than for a second one written here.
    """
    spy = _gr2l(monkeypatch)
    weather = SpyWeather()
    result = _plot(
        make_plot_timeseries_tool(_context(weather=weather)),
        series=[{"source": "model", "variable": "swc_pct", "roof": roof}],
    )

    assert result["status"] == "not_available"
    assert result["reason"] in set(NON_MODELLABLE_ROOFS.values())
    assert "can still be queried" in result["reason"]
    # A scope limit is decided before the work, not after it.
    assert weather.calls == []
    assert spy.rows == []


def test_a_declined_plot_declines_before_it_draws_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The abstention is the plot's, so the legal series beside it is not drawn either.

    A half-drawn chart would be worse than none: the answer would report a scope
    limit while the renderer had been handed a rain series to draw under it.
    """
    spy = _gr2l(monkeypatch)
    result = _plot(
        make_plot_timeseries_tool(_context(weather=SpyWeather())),
        series=[RAIN, {"source": "model", "variable": "swc_pct", "roof": "gravel"}],
    )
    assert result["status"] == "not_available"
    assert "series" not in result
    assert spy.rows == []


@pytest.mark.parametrize(
    ("roof", "table", "variable"),
    [("gravel", "swc", "soil_moisture"), ("wetland", "outflow", "outflow")],
)
def test_the_measured_series_of_those_roofs_stay_valid(
    roof: str, table: str, variable: str
) -> None:
    """Out of the model's scope is not out of the record: their sensors plot normally."""
    result = _plot(
        make_plot_timeseries_tool(_context()),
        series=[{"source": "measured", "table": table, "variable": variable, "roof": roof}],
    )
    assert result["status"] == "success"
    assert result["series"][0]["roof"] == roof
    assert result["series"][0]["stats"]["points"] > 0


def test_a_window_past_the_horizon_is_a_scope_limit_for_the_live_sources_only() -> None:
    """No weather exists that far out, so a chart of it would be a chart of nothing."""
    as_of = datetime(2025, 7, 1, 12, 0, tzinfo=BERLIN)
    tool = make_plot_timeseries_tool(_context(as_of, weather=SpyWeather()))
    far = {"start_date": "2025-07-01", "end_date": "2025-08-30"}

    forecast = asyncio.run(tool(series=[FORECAST_RAIN], **far))
    assert forecast["status"] == "not_available"
    assert "16 days ahead" in forecast["reason"]

    # The measured record has no horizon — it simply stops, which is a gap.
    measured = asyncio.run(tool(series=[RAIN], **far))
    assert measured["status"] == "success"
    assert measured["series"][0]["truncated"] is True


# --- The resolved spec, echoed in full (T092) ---------------------------------


def test_the_resolved_spec_carries_every_field_the_specification_lists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Source and variable per series, the resolved range, the derived fields, the flags.

    Pinned as an exact key set rather than by spot-checking, because "in full" is
    the requirement: a field silently dropped from the echo is a field the
    scorer's argument checks and the answer's caveats both lose at once.
    """
    _gr2l(monkeypatch)
    result = _plot(
        make_plot_timeseries_tool(_context(weather=SpyWeather(source="station"))),
        series=[RAIN, FORECAST_RAIN, MODELLED_MOISTURE],
        kind="model_overlay",
    )

    assert result["status"] == "success"
    assert set(result) == {
        "status",
        "kind",
        "artifact_ref",
        "start",
        "end",
        "resolution",
        "series",
    }
    assert (result["start"], result["end"]) == WINDOW
    assert result["kind"] == "model_overlay"
    # Mixed plot, so the measured half was redrawn as days (T094).
    assert result["resolution"] == "daily"

    for series in result["series"]:
        assert set(series) == {
            "source",
            "variable",
            "table",
            "roof",
            "column",
            "quantity",
            "aggregation",
            "unit",
            "axis",
            "note",
            "weather_source",
            "initial_soil_moisture_pct",
            "albedo",
            "forcings",
            "seed",
            "gaps",
            "truncated",
            "stats",
        }
        assert set(series["stats"]) == {
            "points",
            "first",
            "last",
            "min",
            "max",
            "mean",
            "total",
        }


def test_a_flux_reports_a_total_and_a_state_does_not() -> None:
    """The accumulate-or-sample distinction again: a sum of water contents is nothing."""
    result = _plot(
        make_plot_timeseries_tool(_context()),
        series=[
            RAIN,
            {"source": "measured", "table": "swc", "variable": "soil_moisture", "roof": "gravel"},
        ],
    )
    rain, moisture = result["series"]
    assert rain["stats"]["total"] is not None
    assert moisture["stats"]["total"] is None
    # The statistics are the whole of what an answer has, so they must be real.
    assert moisture["stats"]["min"] <= moisture["stats"]["mean"] <= moisture["stats"]["max"]


def test_the_statistics_are_the_drawn_points_own() -> None:
    """Hand-computed from the raw rows, so a summary over the wrong series fails here."""
    start, end = WINDOW
    result = _plot(make_plot_timeseries_tool(_context()), series=[RAIN])
    stats = result["series"][0]["stats"]

    raw = [value for _, value in _raw_rows("wetter", "Rain")]
    expected = [
        value
        for at, value in _raw_rows("wetter", "Rain")
        if date.fromisoformat(start)
        <= at.replace(tzinfo=UTC).astimezone(BERLIN).date()
        <= date.fromisoformat(end)
    ]
    assert raw != expected, "the window must actually select a subset"
    assert stats["points"] == len(expected)
    assert stats["total"] == pytest.approx(round(sum(expected), 3))
    assert stats["max"] == pytest.approx(round(max(expected), 3))


def test_a_series_that_stops_before_the_window_does_is_flagged_truncated() -> None:
    """The as-of cut is the case's own edge, and a reader has to be told about it."""
    cut = datetime(2025, 7, 3, 12, 0, tzinfo=BERLIN)
    result = _plot(make_plot_timeseries_tool(_context(cut)), series=[RAIN])

    series = result["series"][0]
    assert series["truncated"] is True
    # 4 July through 7 July were never in the view this case reads.
    assert series["gaps"] == 4
    assert series["stats"]["last"].startswith("2025-07-03")


def test_a_modelled_flux_opens_on_day_two_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The seed day computes no runoff, so that day is a gap rather than a zero."""
    _gr2l(monkeypatch)
    result = _plot(
        make_plot_timeseries_tool(_context(weather=SpyWeather())),
        series=[{"source": "model", "variable": "OUT", "roof": MODELLED_ROOF}],
    )

    series = result["series"][0]
    assert series["gaps"] == 1
    assert series["truncated"] is False
    assert series["stats"]["first"] == "2025-07-02"
    assert series["note"] is not None and "seeds the stores" in series["note"]


def test_a_table_outside_its_own_cover_comes_back_empty_and_flagged() -> None:
    """`radiation` runs 2025-03-01 to 2025-10-01; a January window is all gap."""
    result = asyncio.run(
        make_plot_timeseries_tool(_context())(
            series=[
                {
                    "source": "measured",
                    "table": "radiation",
                    "variable": "shortwave_down",
                    "roof": "gravel",
                }
            ],
            start_date="2026-01-01",
            end_date="2026-01-07",
        )
    )
    series = result["series"][0]
    assert series["stats"]["points"] == 0
    assert (series["gaps"], series["truncated"]) == (7, True)


# --- No series to the model, and a handle instead (T093) ----------------------


def test_no_value_reaches_the_model_anywhere_in_the_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Asserted by shape, not by name: **no field of a series is a list of anything**.

    Stronger than checking for a ``points`` key, which is what a future field
    holding values would simply be called something else than. The one list in
    the payload is the list of series itself, and ``stats.points`` — a count — is
    the only place the word appears.
    """
    _gr2l(monkeypatch)
    result = _plot(
        make_plot_timeseries_tool(_context(weather=SpyWeather())),
        series=[RAIN, FORECAST_RAIN, MODELLED_MOISTURE],
    )

    assert result["status"] == "success"
    assert [key for key, value in result.items() if isinstance(value, list)] == ["series"]
    for series in result["series"]:
        assert not [value for value in series.values() if isinstance(value, list)]
        assert isinstance(series["stats"]["points"], int)
    assert result["artifact_ref"].startswith("plot_")


def test_the_handle_is_the_resolved_specs_own_and_not_a_fresh_one_per_call() -> None:
    """A rollout has to replay identically, so the handle is content, not chance."""
    tool = make_plot_timeseries_tool(_context())
    first = _plot(tool, series=[RAIN])
    again = _plot(tool, series=[RAIN])
    elsewhere = asyncio.run(
        tool(series=[RAIN], start_date="2025-07-02", end_date="2025-07-08")
    )

    assert first == again
    assert first["artifact_ref"] != elsewhere["artifact_ref"]


# --- The headless handoff (T097) ----------------------------------------------


def test_an_evaluation_call_writes_no_state_key() -> None:
    """The harness calls the function directly, so the render path does not exist for it."""
    tool = make_plot_timeseries_tool(_context())
    result = _plot(tool, series=[RAIN])
    assert result["status"] == "success"
    assert "tool_context" in inspect.signature(tool).parameters


def test_the_series_is_stashed_for_the_wrapper_and_kept_out_of_the_result() -> None:
    """Two consumers, one call: the model gets the spec, the renderer gets the points."""
    state: dict[str, Any] = {}
    tool = make_plot_timeseries_tool(_context())
    result = asyncio.run(
        tool(
            series=[RAIN],
            start_date=WINDOW[0],
            end_date=WINDOW[1],
            tool_context=SimpleNamespace(state=state),
        )
    )

    stashed = state[PLOT_SERIES_STATE_KEY]
    assert stashed["artifact_ref"] == result["artifact_ref"]
    assert stashed["series"][0]["variable"] == "precipitation"
    assert len(stashed["series"][0]["points"]) == result["series"][0]["stats"]["points"]
    # The same points, and none of them in the model's copy.
    assert not [value for value in result["series"][0].values() if isinstance(value, list)]


def test_the_wrapper_merges_the_stash_into_the_payload_the_chat_reads() -> None:
    """The join is the wrapper's whole job: one payload carrying spec and values."""
    state: dict[str, Any] = {}
    tool = make_plot_timeseries_tool(_context())
    result = asyncio.run(
        tool(
            series=[RAIN],
            start_date=WINDOW[0],
            end_date=WINDOW[1],
            tool_context=SimpleNamespace(state=state),
        )
    )

    payload = merge_plot_payload(state, result, state[PLOT_SERIES_STATE_KEY])

    assert payload is state[PLOT_PAYLOAD_STATE_KEY]
    assert payload["artifact_ref"] == result["artifact_ref"]
    drawn = payload["series"][0]
    assert drawn["unit"] == "mm"
    assert len(drawn["points"]) == result["series"][0]["stats"]["points"]
    assert drawn["points"][0][0].startswith("2025-07-01")


def test_a_stash_from_another_plot_is_never_rendered_under_this_ones_spec() -> None:
    """Joined on the handle, not on "the last thing stashed"."""
    state: dict[str, Any] = {}
    result = {"status": "success", "artifact_ref": "plot_aaaa", "series": [{"source": "measured"}]}
    stale = {"artifact_ref": "plot_bbbb", "series": [{"source": "measured", "points": [["d", 1.0]]}]}

    assert merge_plot_payload(state, result, stale) is None
    assert merge_plot_payload(state, {"status": "error"}, None) is None
    assert state == {}


def test_the_toolset_wraps_the_plot_tool_and_nothing_else_new() -> None:
    """The wrapper is how the series reaches the chat, so it has to be wired in."""
    from water_assistant_agent.assistant.toolset import PLOT_TOOL, TOOL_NAMES, build_toolset

    tools = build_toolset(_context(weather=SpyWeather()))
    plot_tool = tools[TOOL_NAMES.index(PLOT_TOOL)]

    assert TOOL_NAMES[-1] == PLOT_TOOL
    assert isinstance(plot_tool, PlotTimeseriesTool)
    assert plot_tool.name == PLOT_TOOL


# --- The packet's exit: no live call in replay (T099) -------------------------


class _FakeGr2lService:
    """The GR2L endpoint, at the one seam ``run_gr2l`` calls out through."""

    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []
        self.enabled = True

    async def __call__(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> Any:
        if not self.enabled:
            raise AssertionError("a replayed plot reached the GR2L service")
        self.posts.append(body)
        return {
            "data": [
                {
                    "Date": row["Date"],
                    "ET_PM": 1.0,
                    "Ssub": 9.0,
                    "Sret": 0.0,
                    "Qdown": None if index == 0 else 0.0,
                    "Qup": None if index == 0 else 0.0,
                    "OUT": None if index == 0 else 0.0,
                    "ET": 0.7,
                }
                for index, row in enumerate(body["data"])
            ]
        }


def test_a_model_and_weather_and_measured_plot_replays_with_no_live_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The packet's exit criterion, on a chart that uses all three sources at once.

    The weather half is the real composite over the pinned database and the
    window is one the station covers whole, so that side needs no cache and no
    network — asserted by ``weather_source == 'station'`` rather than assumed.
    The model half is served from the entry the recording pass committed, keyed
    on the request ``run_gr2l`` actually builds. Proved by a service that raises:
    the replayed plot cannot have called it and still succeeded.

    The canary is part of the claim. It is fetched on the recording pass — before
    any entry is written — and not on the replay, because a hit returns before
    the canary is consulted: there is no service to have moved when nothing is
    asked of it.
    """
    service = _FakeGr2lService()
    monkeypatch.setattr(gr2l_client_module, "_post_gr2l", service)
    monkeypatch.setattr(
        gr2l_client_module,
        "get_settings",
        lambda: type("S", (), {"gr2l_api_base_url": "http://gr2l.test", "gr2l_api_key": "k"})(),
    )

    context = _context(
        cache=ResponseCache(tmp_path / "cache"),
        factory=lambda db, http_cache: make_weather_client(db, http_cache, allow_live=False),
    )
    tool = make_plot_timeseries_tool(context)
    series = [RAIN, FORECAST_RAIN, MODELLED_MOISTURE]

    recorded = _plot(tool, series=series, kind="model_overlay")

    assert recorded["status"] == "success"
    assert [item["weather_source"] for item in recorded["series"]] == [
        None,
        "station",
        "station",
    ], "the window must be one the station covers, or the plot called Open-Meteo"
    # The data request and the canary probe, and nothing else.
    assert len(service.posts) == 2
    assert gr2l_client_module.CANARY_REQUEST in service.posts

    service.enabled = False
    replayed = _plot(tool, series=series, kind="model_overlay")

    assert replayed == recorded
    assert len(service.posts) == 2, "the replay issued a live call"


def test_the_replayed_plot_is_the_cache_and_not_a_service_that_is_never_called(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The other half of the claim: an uncached window in the same setup fails loudly."""
    service = _FakeGr2lService()
    service.enabled = False
    monkeypatch.setattr(gr2l_client_module, "_post_gr2l", service)
    monkeypatch.setattr(
        gr2l_client_module,
        "get_settings",
        lambda: type("S", (), {"gr2l_api_base_url": "http://gr2l.test", "gr2l_api_key": "k"})(),
    )

    context = _context(
        cache=ResponseCache(tmp_path / "cache"),
        factory=lambda db, http_cache: make_weather_client(db, http_cache, allow_live=False),
    )
    result = _plot(make_plot_timeseries_tool(context), series=[MODELLED_MOISTURE])

    assert result["status"] == "error"
    assert result["error_type"] == "upstream"
