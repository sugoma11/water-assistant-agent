"""The three pilot oracles, against answers computed by hand (T103).

Two kinds of assertion, and both are needed.

**Hand-computed answers.** Each oracle is run over a surface small enough to
work out on paper — three outflow rows straddling a day boundary, a flat GR2L
series, a seed placed on a known rung — so the test says what the answer *is*
rather than what the code happens to produce. An oracle checked only against the
tool would agree with the tool about a shared mistake.

**Agreement with the tool.** T07 and T09 are the two whose gold trajectory is a
single deterministic tool, so oracle and tool can be run over one context and
compared directly. This is the claim T103 rests on — an oracle that drifted from
the tool would encode as truth a number no route could produce — and it is
checked rather than argued. T01's counterpart is a language model writing SQL,
so it has no such twin; its day-boundary fixture is the substitute.
"""

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest
from zoneinfo import ZoneInfo

from eval.oracles import ORACLES
from eval.oracles.base import OracleInputError
from eval.oracles.irrigation import t07_needs_irrigation_now
from eval.oracles.model_chain import forecast_days_for, t09_falls_below_threshold
from eval.oracles.pins import duckdb_sha256, station_derivation_version
from eval.oracles.sql import month_window, t01_total_outflow
from water_assistant_agent.assistant.agents.text_to_sql.executor import (
    DuckDbQueryExecutor,
    create_duckdb_connection,
)
from water_assistant_agent.assistant.context import ScenarioContext
from water_assistant_agent.assistant.irrigation import Regime
from water_assistant_agent.assistant.rules_constants import rules_for
from water_assistant_agent.assistant.tools import gr2l as gr2l_module
from water_assistant_agent.assistant.tools.gr2l import make_green_roof_balance_tool
from water_assistant_agent.assistant.tools.gr2l_client import ROOF_PRESETS
from water_assistant_agent.assistant.tools.irrigation import make_irrigation_tool
from water_assistant_agent.assistant.tools.roofs import ROOFS
from water_assistant_agent.assistant.tools.schemas import (
    DailyWeatherRow,
    Gr2lResultRow,
    WeatherResult,
)
from water_assistant_agent.assistant.tools.swc import mm_to_theta_pct
from harness.run_case import make_case_context

BERLIN = ZoneInfo("Europe/Berlin")
PINNED_DB = "data/water.duckdb"

# Inside every record and late enough that each roof has a fresh seed: swc runs
# to 2026-04-24 and the station's complete days to 2026-04-26.
AS_OF = datetime(2026, 4, 20, 12, 0, tzinfo=BERLIN)

MODELLABLE = "non_irrigated_extensive"
IRRIGABLE = "irrigated_extensive"


# --- Doubles ------------------------------------------------------------------


class StubWeather:
    """A flat forecast: the same day repeated over whatever window is asked for."""

    def __init__(self, *, precip: float = 0.0, tx: float = 30.0, source: str = "station"):
        self.calls: list[tuple[str, str]] = []
        self._precip, self._tx, self._source = precip, tx, source

    async def fetch(self, *, start_date: str, end_date: str) -> WeatherResult:
        self.calls.append((start_date, end_date))
        start = datetime.fromisoformat(start_date).date()
        end = datetime.fromisoformat(end_date).date()
        return WeatherResult(
            latitude=51.353484,
            longitude=12.432152,
            timezone="Europe/Berlin",
            source=self._source,
            data=[
                DailyWeatherRow(
                    Date=(start + timedelta(days=offset)).isoformat(),
                    tm=self._tx - 6.0,
                    tx=self._tx,
                    tn=self._tx - 12.0,
                    rf=55.0,
                    precip=self._precip,
                    w=6.0,
                    gs=2400.0,
                )
                for offset in range((end - start).days + 1)
            ],
        )


class StubGr2l:
    """GR2L held at one storage value, so every %θ downstream is hand-computable."""

    def __init__(self, ssub: float = 10.0, step: float = 0.0) -> None:
        self.calls = 0
        self._ssub, self._step = ssub, step

    async def __call__(self, rows: list[DailyWeatherRow], parameters: Any, **_: Any) -> Any:
        self.calls += 1
        return [
            Gr2lResultRow(
                Date=row.Date,
                ET_PM=1.0,
                Ssub=self._ssub + index * self._step,
                Sret=0.0,
                Qdown=None if index == 0 else 0.0,
                Qup=None if index == 0 else 0.0,
                OUT=None if index == 0 else 0.0,
                ET=0.8,
            )
            for index, row in enumerate(rows)
        ]


def _executor(path: Path, statements: str) -> DuckDbQueryExecutor:
    """A read-only executor over a throwaway database built from *statements*."""
    with duckdb.connect(str(path)) as connection:
        connection.execute(statements)
    return DuckDbQueryExecutor(connection_factory=lambda: create_duckdb_connection(str(path)))


def _context(
    *, as_of: datetime = AS_OF, db: Any = None, weather: Any = None
) -> ScenarioContext:
    if db is not None:
        return ScenarioContext.bound(
            clock=lambda: as_of, db=db, weather=weather, cache=None
        )
    return ScenarioContext(
        clock=lambda: as_of,
        db_path=PINNED_DB,
        weather_client_factory=lambda _db, _cache: weather,
        http_cache=None,
    )


def _inputs(template: str, **params: Any) -> dict[str, Any]:
    return {
        "case_id": f"{template}-0001",
        "template_id": template,
        "question": "…",
        "as_of": AS_OF.isoformat(),
        "params": params,
    }


# --- T01: the sum, and the day it is summed over ------------------------------

# Three half-hourly rows in naive UTC, chosen so the Berlin day and the UTC day
# put two of them in different months:
#
#   2025-06-30 22:30 UTC = 2025-07-01 00:30 Berlin -> July  (UTC says June)
#   2025-07-15 12:00 UTC = 2025-07-15 14:00 Berlin -> July  (both agree)
#   2025-07-31 22:30 UTC = 2025-08-01 00:30 Berlin -> August (UTC says July)
#
# July totals 1.0 + 2.5 = 3.5 L on Berlin days and 2.5 + 4.0 = 6.5 L on UTC ones.
_OUTFLOW_ROWS = """
CREATE TABLE outflow (timestamp TIMESTAMP, Kies_Efflux DOUBLE);
INSERT INTO outflow VALUES
  (TIMESTAMP '2025-06-30 22:30:00', 1.0),
  (TIMESTAMP '2025-07-15 12:00:00', 2.5),
  (TIMESTAMP '2025-07-31 22:30:00', 4.0);
"""

BERLIN_JULY_TOTAL = 3.5
UTC_JULY_TOTAL = 6.5


def test_t01_sums_the_roofs_own_column_over_the_sites_own_days(tmp_path: Path):
    """3.5 L, worked out from three rows — and not the 6.5 L a UTC grouping gives.

    The two totals differ because two of the three rows fall on different
    calendar days in the two timezones. ``decisions.md`` § The day boundary is
    what makes 3.5 the answer, and the same expression is in the schema block the
    candidate is shown (T106), so the two group alike by construction.
    """
    ctx = _context(as_of=datetime(2026, 1, 1, 12, 0, tzinfo=BERLIN), db=_executor(tmp_path / "t01.duckdb", _OUTFLOW_ROWS))

    answer = asyncio.run(t01_total_outflow(_inputs("T01", roof="gravel", month="2025-07"), ctx))

    assert answer.answer == BERLIN_JULY_TOTAL
    assert answer.answer != UTC_JULY_TOTAL
    assert answer.unit == "L"
    assert answer.status == "answered"


def test_t01_accepts_the_roofs_german_alias(tmp_path: Path):
    """"Kiesdach" reaches ``Kies_Efflux``, through ``roofs.py``'s map and no other.

    Questions name roofs in alias-covered natural language (``questions.md``
    §1.6), so an oracle that only understood canonical names would refuse half
    its own cases.
    """
    ctx = _context(as_of=datetime(2026, 1, 1, 12, 0, tzinfo=BERLIN), db=_executor(tmp_path / "t01de.duckdb", _OUTFLOW_ROWS))

    answer = asyncio.run(t01_total_outflow(_inputs("T01", roof="Kiesdach", month="2025-07"), ctx))

    assert answer.answer == BERLIN_JULY_TOTAL
    assert answer.detail["column"] == "Kies_Efflux"


def test_t01_refuses_a_month_the_case_cannot_see(tmp_path: Path):
    """A month still running at ``as_of`` has no single defensible total.

    The harness's period check reads *days* out of parameter values and
    ``"2025-07"`` carries none, so this containment exists nowhere else. Half of
    July answered as "July" is an ambiguous oracle, which §1.6 discards.
    """
    ctx = _context(as_of=datetime(2025, 7, 15, 12, 0, tzinfo=BERLIN), db=_executor(tmp_path / "t01cut.duckdb", _OUTFLOW_ROWS))

    with pytest.raises(OracleInputError, match="past the case's as_of"):
        asyncio.run(t01_total_outflow(_inputs("T01", roof="gravel", month="2025-07"), ctx))


def test_t01_refuses_a_roof_with_no_lysimeter(tmp_path: Path):
    """The semi-intensive roof is outside pool P1f and has no column to sum.

    It is refused by reading ``roofs.py`` — the roof simply has no ``outflow``
    key — rather than by a hand-written pool list that could fall out of step
    with the instrumentation.
    """
    ctx = _context(db=_executor(tmp_path / "t01pool.duckdb", _OUTFLOW_ROWS))

    with pytest.raises(OracleInputError, match="no lysimeter"):
        asyncio.run(
            t01_total_outflow(_inputs("T01", roof="semi_intensive", month="2025-07"), ctx)
        )
    assert "outflow" not in ROOFS["semi_intensive"].columns


@pytest.mark.parametrize("month", ["2025-13", "July 2025", "2025-07-01", "2025"])
def test_t01_refuses_a_month_it_cannot_read(month: str, tmp_path: Path):
    """A malformed month fails at the parameter, not five frames into arithmetic."""
    ctx = _context(db=_executor(tmp_path / f"t01m{abs(hash(month))}.duckdb", _OUTFLOW_ROWS))

    with pytest.raises(OracleInputError, match="YYYY-MM"):
        asyncio.run(t01_total_outflow(_inputs("T01", roof="gravel", month=month), ctx))


@pytest.mark.parametrize(
    ("month", "expected"),
    [("2025-02", ("2025-02-01", "2025-02-28")), ("2024-02", ("2024-02-01", "2024-02-29"))],
)
def test_month_window_knows_about_february(month: str, expected: tuple[str, str]):
    """Leap years, from the calendar module rather than from a table of lengths."""
    start, end = month_window(month)

    assert (start.isoformat(), end.isoformat()) == expected


def test_t01_runs_against_the_real_pinned_database():
    """46.0 L of gravel-roof outflow in July 2025, through the harness's own context.

    Every other T01 test builds a three-row table, which proves the arithmetic
    and proves nothing about the schema: a column renamed in ``water.duckdb``, or
    a query this oracle writes that DuckDB rejects, would pass all of them. This
    one reads the pinned database through ``make_case_context`` — the same
    as-of executor a rollout gets — and pins the number it returns.
    """
    ctx = make_case_context(AS_OF)

    answer = asyncio.run(
        t01_total_outflow(_inputs("T01", roof="Kiesdach", month="2025-07"), ctx)
    )

    assert answer.answer == pytest.approx(46.0)
    assert answer.pins["duckdb_sha256"] == duckdb_sha256()


# --- T07: the ladder, on the rung the fixture puts it on ----------------------


def _seed_db(path: Path, roof: str, theta_pct: float) -> DuckDbQueryExecutor:
    """A ``swc`` table holding one reading for *roof*, the day before ``AS_OF``."""
    column = ROOFS[roof].columns["swc"]
    return _executor(
        path,
        f"CREATE TABLE swc (timestamp TIMESTAMP, {column} DOUBLE);"
        f"INSERT INTO swc VALUES (TIMESTAMP '2026-04-19 12:00:00', {theta_pct});",
    )


def _theta_at(roof: str, threshold: str) -> float:
    """The %θ at which *roof*'s store sits exactly on one of its thresholds."""
    rules = rules_for(roof)
    thresholds = Regime.MILLIMETRES.thresholds(rules)
    return mm_to_theta_pct(getattr(thresholds, threshold), rules.substrate_height_cm)


def test_t07_irrigates_a_roof_below_its_wilting_point(tmp_path: Path):
    """Rung 1, and it outranks everything below it — including cold weather.

    The seed step takes the measurement unchanged (``simulate_store`` property
    3), so the window's minimum is at most the seed; a seed under the wilting
    point therefore decides the case whatever the forecast does, and the answer
    is ``True`` by the ladder alone.
    """
    theta = _theta_at(IRRIGABLE, "wilting") - 1.0
    ctx = _context(
        db=_seed_db(tmp_path / "t07dry.duckdb", IRRIGABLE, theta),
        weather=StubWeather(precip=0.0, tx=5.0),
    )

    answer = asyncio.run(t07_needs_irrigation_now(_inputs("T07", roof=IRRIGABLE), ctx))

    assert answer.answer is True
    assert answer.detail["reason"] == "below_wilting_point"
    assert answer.unit is None


def test_t07_declines_a_roof_that_is_merely_warm_enough_to_be_cool(tmp_path: Path):
    """Rung 2: no heat is forecast, so no cooling is wanted and the roof is not critical.

    Ten %θ above the wilting point with no rain and a 5 °C week: two days of ET
    cannot cross that margin, so the minimum stays above the wilting point and
    the second rung decides.
    """
    theta = _theta_at(IRRIGABLE, "wilting") + 10.0
    ctx = _context(
        db=_seed_db(tmp_path / "t07cold.duckdb", IRRIGABLE, theta),
        weather=StubWeather(precip=0.0, tx=5.0),
    )

    answer = asyncio.run(t07_needs_irrigation_now(_inputs("T07", roof=IRRIGABLE), ctx))

    assert answer.answer is False
    assert answer.detail["reason"] == "no_heat_no_stress"


def test_t07_asks_for_the_week_the_refill_rung_reads(tmp_path: Path):
    """The window is the refill horizon from today — seven days, starting at ``as_of``.

    The decision is about now, so the tool takes no date arguments; an oracle
    that fetched a different week would read a different fourth rung.
    """
    weather = StubWeather()
    ctx = _context(
        db=_seed_db(tmp_path / "t07win.duckdb", IRRIGABLE, 20.0), weather=weather
    )

    asyncio.run(t07_needs_irrigation_now(_inputs("T07", roof=IRRIGABLE), ctx))

    assert weather.calls == [("2026-04-20", "2026-04-26")]


@pytest.mark.parametrize("tx", [5.0, 30.0])
@pytest.mark.parametrize("precip", [0.0, 12.0])
@pytest.mark.parametrize("offset", [-1.0, 2.0, 10.0])
def test_t07_agrees_with_calc_irrigation_on_the_same_context(
    tx: float, precip: float, offset: float, tmp_path: Path
):
    """The claim T103 rests on, checked across the ladder rather than argued.

    Twelve forcings spanning heat, rain and three seeds — one under the wilting
    point, one just over it, one well above — run through the oracle and through
    the tool the case's gold trajectory names. They share ``run_roof`` and so
    ``irrigation_decision``; if a later edit gave either its own copy of a step,
    this is what would fail.
    """
    theta = _theta_at(IRRIGABLE, "wilting") + offset
    path = tmp_path / f"t07agree{abs(hash((tx, precip, offset)))}.duckdb"
    ctx = _context(
        db=_seed_db(path, IRRIGABLE, theta), weather=StubWeather(precip=precip, tx=tx)
    )

    answer = asyncio.run(t07_needs_irrigation_now(_inputs("T07", roof=IRRIGABLE), ctx))
    tool = asyncio.run(make_irrigation_tool(ctx)(IRRIGABLE))

    assert tool["status"] == "success"
    assert answer.answer == tool["irrigate"]
    assert answer.detail["reason"] == tool["reason"]


def test_t07_refuses_a_roof_the_rule_declines(tmp_path: Path):
    """The gravel roof and the wetland are outside pool P2 and outside the rule.

    A ``not_available`` is the tool's honest answer and T17/T18's subject; it is
    not T07's, whose pool excludes both — so reaching here is a sampling defect.
    """
    ctx = _context(db=_seed_db(tmp_path / "t07scope.duckdb", IRRIGABLE, 20.0))

    with pytest.raises(OracleInputError, match="P2"):
        asyncio.run(t07_needs_irrigation_now(_inputs("T07", roof="Kiesdach"), ctx))


# --- T09: the modelled minimum against the threshold --------------------------

# GR2L held flat at 10 mm of substrate storage. The extensive roofs are 7 cm
# deep, so 10 mm is 10 / 70 = 14.2857… %θ, rounded to 14.29 by `_to_days`.
FLAT_SSUB_MM = 10.0
FLAT_SWC_PCT = round(mm_to_theta_pct(FLAT_SSUB_MM, float(ROOF_PRESETS[MODELLABLE]["SH"])), 2)


@pytest.mark.parametrize(
    ("threshold", "expected"),
    [(FLAT_SWC_PCT + 1.0, True), (FLAT_SWC_PCT - 1.0, False), (FLAT_SWC_PCT, False)],
)
def test_t09_compares_the_modelled_minimum_with_the_threshold(
    threshold: float, expected: bool, monkeypatch: pytest.MonkeyPatch
):
    """14.29 %θ held flat: below 15.29, not below 13.29, and not below itself.

    The third case pins the comparison as **strict** — "fall below" a threshold
    the roof sits exactly on is ``False``. §1.6's oracle filter keeps a real case
    away from its own boundary, so this is a statement about the rule rather than
    about a case that will be sampled.
    """
    monkeypatch.setattr(gr2l_module, "run_gr2l", StubGr2l(ssub=FLAT_SSUB_MM))
    ctx = _context(weather=StubWeather())

    answer = asyncio.run(
        t09_falls_below_threshold(_inputs("T09", roof=MODELLABLE, thr=threshold, d=3), ctx)
    )

    assert answer.detail["min_swc_pct"] == FLAT_SWC_PCT
    assert answer.answer is expected
    assert answer.unit is None


def test_t09_reads_the_minimum_over_a_falling_series(monkeypatch: pytest.MonkeyPatch):
    """A ramp down: the answer is the last day's value, not the first day's.

    Seeded at 20 mm and losing 5 mm a day, the three-day window's minimum is
    10 mm — 14.29 %θ — while day 1 sits at 28.57 %θ. An oracle reading the seed
    instead of the series would answer the opposite of this case.
    """
    monkeypatch.setattr(gr2l_module, "run_gr2l", StubGr2l(ssub=20.0, step=-5.0))
    ctx = _context(weather=StubWeather())

    answer = asyncio.run(
        t09_falls_below_threshold(_inputs("T09", roof=MODELLABLE, thr=15.0, d=3), ctx)
    )

    assert answer.detail["min_swc_pct"] == FLAT_SWC_PCT
    assert answer.detail["days"] == 3
    assert answer.answer is True


def test_t09_resolves_the_horizon_the_way_the_tool_does(monkeypatch: pytest.MonkeyPatch):
    """"The next 3 days" is today and the two days after it, not four days.

    Both sides go through ``resolve_window``, so the window a case is scored on
    and the window it was answered over are one resolution rather than two.

    This test is why T09 is phrased in days. It passed when the question said
    "72 hours" too — the *oracle* always resolved three days — but the pilot's
    candidate, writing the same phrase as explicit dates, sent four, and nothing
    on this side could see the disagreement (T107).
    """
    monkeypatch.setattr(gr2l_module, "run_gr2l", StubGr2l())
    weather = StubWeather()
    ctx = _context(weather=weather)

    asyncio.run(
        t09_falls_below_threshold(_inputs("T09", roof=MODELLABLE, thr=1.0, d=3), ctx)
    )

    assert weather.calls == [("2026-04-20", "2026-04-22")]


@pytest.mark.parametrize("days", [0, -3, 1.5, "3", True])
def test_t09_refuses_a_horizon_that_is_not_whole_days(days: object):
    """The horizon is a positive whole day count, and nothing here rounds one.

    ``True`` is in the list because ``isinstance(True, int)`` is ``True`` in
    Python: a bool reaching this argument is a template fault, and simulating one
    day because a flag was passed would be the silent kind.
    """
    with pytest.raises(OracleInputError, match="whole number of days"):
        forecast_days_for(days)


@pytest.mark.parametrize("threshold", [5.0, 14.29, 25.0])
def test_t09_agrees_with_the_water_balance_tool_on_the_same_context(
    threshold: float, monkeypatch: pytest.MonkeyPatch
):
    """The same claim as T07's, on the model chain.

    The tool reports the run's minimum in its summary and the oracle compares its
    own; both come from one ``run_gr2l`` call over one window, so a divergence in
    the seed rule, the window or the mm→%θ conversion shows up as a disagreement
    here.
    """
    monkeypatch.setattr(gr2l_module, "run_gr2l", StubGr2l(ssub=20.0, step=-5.0))
    ctx = _context(weather=StubWeather())

    answer = asyncio.run(
        t09_falls_below_threshold(
            _inputs("T09", roof=MODELLABLE, thr=threshold, d=3), ctx
        )
    )
    tool = asyncio.run(make_green_roof_balance_tool(ctx)(MODELLABLE, forecast_days=3))

    assert tool["status"] == "success"
    assert answer.detail["min_swc_pct"] == tool["summary"]["min_swc_pct"]
    assert answer.answer is (tool["summary"]["min_swc_pct"] < threshold)


def test_t09_refuses_a_roof_the_water_balance_declines(monkeypatch: pytest.MonkeyPatch):
    """Gravel has no substrate store and the wetland is declined at layer 1."""
    monkeypatch.setattr(gr2l_module, "run_gr2l", StubGr2l())
    ctx = _context(weather=StubWeather())

    for roof in ("Kiesdach", "Sumpfdach"):
        with pytest.raises(OracleInputError, match="P2"):
            asyncio.run(
                t09_falls_below_threshold(_inputs("T09", roof=roof, thr=10.0, d=3), ctx)
            )


# --- Pins: stamped where they were read ---------------------------------------


def test_t01_stamps_the_database_and_nothing_else(tmp_path: Path):
    """No weather was read and no model was run, so neither is claimed.

    A blanket stamp would assert a dependency the answer does not have, and would
    then survive a change that could not possibly have moved it.
    """
    ctx = _context(as_of=datetime(2026, 1, 1, 12, 0, tzinfo=BERLIN), db=_executor(tmp_path / "t01pins.duckdb", _OUTFLOW_ROWS))

    pins = asyncio.run(
        t01_total_outflow(_inputs("T01", roof="gravel", month="2025-07"), ctx)
    ).pins

    assert set(pins) == {"duckdb_sha256"}
    assert pins["duckdb_sha256"] == duckdb_sha256()


def test_t07_stamps_the_station_derivation_when_the_station_answered(tmp_path: Path):
    """The derivation the database hash does not cover, stamped when it was used.

    ``station`` means the window was aggregated from the site's own rows by
    ``weather_station``'s derivation, so a change to that aggregation moves this
    answer while ``water.duckdb`` stays byte-identical.
    """
    ctx = _context(
        db=_seed_db(tmp_path / "t07pins.duckdb", IRRIGABLE, 20.0),
        weather=StubWeather(source="station"),
    )

    pins = asyncio.run(t07_needs_irrigation_now(_inputs("T07", roof=IRRIGABLE), ctx)).pins

    assert pins["weather_source"] == ["station"]
    assert pins["station_derivation"] == station_derivation_version()
    assert "gr2l_canary" not in pins


def test_t07_leaves_the_derivation_unstamped_when_archive_answered(tmp_path: Path):
    """Archive rows never pass through the derivation, so claiming it would be false."""
    ctx = _context(
        db=_seed_db(tmp_path / "t07arch.duckdb", IRRIGABLE, 20.0),
        weather=StubWeather(source="archive"),
    )

    pins = asyncio.run(t07_needs_irrigation_now(_inputs("T07", roof=IRRIGABLE), ctx)).pins

    assert pins["weather_source"] == ["archive"]
    assert "station_derivation" not in pins


def test_t09_stamps_the_gr2l_canary(monkeypatch: pytest.MonkeyPatch):
    """A modelled answer is only truth relative to the model build that produced it.

    The canary is the committed capture (T115); an oracle that could recompute it
    would be calling the service twice per case.
    """
    monkeypatch.setattr(gr2l_module, "run_gr2l", StubGr2l())
    ctx = _context(weather=StubWeather())

    pins = asyncio.run(
        t09_falls_below_threshold(_inputs("T09", roof=MODELLABLE, thr=10.0, d=3), ctx)
    ).pins

    assert len(pins["gr2l_canary"]) == 64
    assert pins["weather_source"] == ["station"]


def test_every_pin_stamped_here_matches_the_case_schema():
    """The four keys ``expectations.pins`` admits, and no fifth.

    ``eval/schema/case.schema.json`` sets ``additionalProperties: false``, so a
    key invented here would fail validation at emission rather than at review —
    but only once a case is emitted, which is T114.
    """
    import json

    schema = json.loads(Path("eval/schema/case.schema.json").read_text(encoding="utf-8"))
    admitted = set(schema["$defs"]["pins"]["properties"])

    assert admitted == {
        "duckdb_sha256",
        "gr2l_canary",
        "station_derivation",
        "weather_source",
    }


# --- The registry -------------------------------------------------------------


def test_the_registry_holds_the_pilot_set_and_only_it():
    """T103 is three templates; T110 adds the rest, family by family.

    Registering an oracle the pilot does not exercise would put an unchecked
    answer one lookup away from a case file.
    """
    assert sorted(ORACLES) == ["T01", "T07", "T09"]
    assert ORACLES["T01"] is t01_total_outflow
    assert ORACLES["T07"] is t07_needs_irrigation_now
    assert ORACLES["T09"] is t09_falls_below_threshold
