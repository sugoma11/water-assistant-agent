"""The oracles, against answers computed by hand (T103, T107, T110).

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
checked rather than argued. Family A's counterpart is a language model writing
SQL, so it has no such twin; its day-boundary fixtures are the substitute, and
each is built so a UTC grouping and a Berlin one give different numbers.

**A third, for the templates whose answer is an outcome rather than a number.**
The abstention templates have no number, so what a test can check is the
*ground* — a card's exclusion, a rule's parameter list, a horizon — and, as
importantly, that the ground has not moved in a way that leaves the case passing
for a different reason than the one it was written for.
"""

import asyncio
import dataclasses
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest
from zoneinfo import ZoneInfo

from eval.oracles import ORACLES
from eval.oracles.base import OracleInputError
from eval.oracles.irrigation import t07_needs_irrigation_now
from eval.oracles.model_chain import (
    forecast_days_for,
    past_days_for,
    t09_falls_below_threshold,
    t10_predicted_minimum,
    t19_model_deviation,
)
from eval.oracles.pins import duckdb_sha256, station_derivation_version
from eval.oracles.presentation import (
    pair_variable,
    t24a_plot_request,
    t24b_extensive_gap,
)
import eval.oracles.reference as reference_module
from eval.oracles.reference import (
    T17B_ROOF,
    t06_stated_constant,
    t17a_absent_constant,
    t17b_scope_near_miss,
)
from eval.oracles.weather import (
    daily_row_fields,
    t13_rain_expected,
    t14_forecast_max_temperature,
    t15b_future_rain,
    t18a_unservable_window,
    t18b_missing_variable,
)
from water_assistant_agent.assistant.knowledge.store import load_card
from water_assistant_agent.assistant.rules_constants import ROOF_RULES
from water_assistant_agent.assistant.tools.weather_client import (
    FORECAST_HORIZON_DAYS,
)
import eval.oracles.sql as sql_module
from eval.oracles.sql import (
    month_window,
    period_window,
    t01_total_outflow,
    t02_hot_day_count,
    t03_irrigation_swc_gap,
    t04_outflow_occurred,
    t05_peak_outflow_day,
    t15a_past_rain,
)
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
from water_assistant_agent.assistant.tools.weather import make_weather_forecast_tool
from water_assistant_agent.assistant.tools.weather_station import StationWeatherSource
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


# --- T02-T05, T15a: the rest of family A (T110) --------------------------------

# Every fixture below straddles the Berlin/UTC day boundary, because that is the
# one property a pure-SQL oracle shares with the candidate rather than owns: the
# schema block tells the model to group by `site_day_expr()` (T106), and a test
# that grouped either way and got the same number would prove nothing about it.

# Berlin Jul 1 holds 33.0 and 32.0; Berlin Jul 2 holds 31.0 (from the 22:30 UTC
# row) and 25.0. Above 30 °C: two Berlin days, one UTC day (Jul 1 absorbs the
# 22:30 row), and three rows — three different numbers for three readings.
_WETTER_ROWS = """
CREATE TABLE wetter (timestamp TIMESTAMP, Tmax DOUBLE, Rain DOUBLE);
INSERT INTO wetter VALUES
  (TIMESTAMP '2025-07-01 12:00:00', 33.0, 1.0),
  (TIMESTAMP '2025-07-01 13:00:00', 32.0, 0.5),
  (TIMESTAMP '2025-07-01 22:30:00', 31.0, 4.0),
  (TIMESTAMP '2025-07-02 12:00:00', 25.0, 0.25);
"""

BERLIN_HOT_DAYS = 2
UTC_HOT_DAYS = 1
HOT_ROWS = 3


def test_t02_counts_days_over_the_threshold_not_rows(tmp_path: Path):
    """Two hot days out of four rows — not three rows and not one UTC day.

    The day's maximum first, then the count of days above the threshold. The
    other order answers "how many half hours were hot", which is a different
    quantity reachable through the same column, and the fixture separates all
    three numbers so no two readings can agree by accident.
    """
    ctx = _context(
        as_of=datetime(2026, 1, 1, 12, 0, tzinfo=BERLIN),
        db=_executor(tmp_path / "t02.duckdb", _WETTER_ROWS),
    )

    answer = asyncio.run(
        t02_hot_day_count(
            _inputs("T02", period="2025-07-01..2025-07-02", thr=30.0), ctx
        )
    )

    assert answer.answer == BERLIN_HOT_DAYS
    assert answer.answer not in (UTC_HOT_DAYS, HOT_ROWS)
    assert answer.unit == "count"
    assert answer.detail["column"] == "Tmax"


def test_t02_exceeding_is_strict(tmp_path: Path):
    """A day sitting exactly on the threshold has not exceeded it.

    §1.6 keeps a sampled draw away from its own boundary, so this decides no
    case that survives generation — it states the rule the oracle applies when
    one does reach it.
    """
    ctx = _context(
        as_of=datetime(2026, 1, 1, 12, 0, tzinfo=BERLIN),
        db=_executor(tmp_path / "t02strict.duckdb", _WETTER_ROWS),
    )

    answer = asyncio.run(
        t02_hot_day_count(
            _inputs("T02", period="2025-07-01..2025-07-02", thr=33.0), ctx
        )
    )

    assert answer.answer == 0


def test_t02_runs_against_the_real_pinned_database():
    """Five days above 29 °C in the first fortnight of July 2025.

    Hand-checkable from the record's own daily maxima: 34.45, 38.10, 29.53,
    26.12, 29.05, 28.47, 24.27, 21.07, 21.40, 25.55, 25.77, 18.82, 24.72,
    29.67 — the 1st, 2nd, 3rd, 5th and 14th clear 29.0 and nothing else does.
    """
    answer = asyncio.run(
        t02_hot_day_count(
            _inputs("T02", period="2025-07-01..2025-07-14", thr=29.0),
            make_case_context(AS_OF),
        )
    )

    assert answer.answer == 5
    assert answer.detail["days_with_rows"] == 14
    assert answer.pins["duckdb_sha256"] == duckdb_sha256()


# Two paired rows and one unpaired. The mean of the paired differences is
# (8 + 4) / 2 = 6.0 pp; the difference of the two columns' means over every
# non-null reading is 22.667 - 13.0 = 9.667. The third row is what separates
# them, and a sensor out for an afternoon is exactly that row.
_SWC_PAIR_ROWS = """
CREATE TABLE swc (timestamp TIMESTAMP, QEx1 DOUBLE, QEx2 DOUBLE);
INSERT INTO swc VALUES
  (TIMESTAMP '2025-07-01 12:00:00', 20.0, 12.0),
  (TIMESTAMP '2025-07-01 12:30:00', 18.0, 14.0),
  (TIMESTAMP '2025-07-01 13:00:00', 30.0, NULL);
"""

PAIRED_GAP_PP = 6.0
UNPAIRED_GAP_PP = 9.667


def test_t03_means_the_difference_over_rows_where_both_roofs_read(tmp_path: Path):
    """6.0 pp, not the 9.667 a difference of two independent means gives.

    The two are the same number only while the columns are non-null on exactly
    the same rows. Requiring both readings is what makes the quantity a gap
    between the roofs *at an instant*, which is what the question names.
    """
    ctx = _context(
        as_of=datetime(2026, 1, 1, 12, 0, tzinfo=BERLIN),
        db=_executor(tmp_path / "t03.duckdb", _SWC_PAIR_ROWS),
    )

    answer = asyncio.run(
        t03_irrigation_swc_gap(_inputs("T03", period="2025-07-01..2025-07-01"), ctx)
    )

    assert answer.answer == PAIRED_GAP_PP
    assert answer.answer != UNPAIRED_GAP_PP
    assert answer.unit == "pp"
    assert answer.detail["paired_rows"] == 2


def test_t03_answers_in_pp_and_not_in_theta():
    """8.377 pp between the extensive roofs over July 2025, on the pinned record.

    A difference between two %θ states is not itself a water content
    (``questions.md`` §1.5), and the unit is what says so — the answer metric
    treats ``%`` and ``%θ`` as one unit and ``pp`` as another.
    """
    answer = asyncio.run(
        t03_irrigation_swc_gap(
            _inputs("T03", period="2025-07-01..2025-07-31"), make_case_context(AS_OF)
        )
    )

    assert answer.answer == pytest.approx(8.377)
    assert answer.unit == "pp"
    # 31 days x 48 half-hours, both roofs reading throughout.
    assert answer.detail["paired_rows"] == 1488


# Berlin Jul 10 gets 5.0; Berlin Jul 12 gets 9.0 (from the 22:30 UTC row) plus
# 1.0 = 10.0. On UTC days the peak is Jul 11 with 9.0, so the two groupings name
# different days rather than the same day by different arithmetic.
_PEAK_ROWS = """
CREATE TABLE outflow (timestamp TIMESTAMP, Kies_Efflux DOUBLE);
INSERT INTO outflow VALUES
  (TIMESTAMP '2025-07-10 12:00:00', 5.0),
  (TIMESTAMP '2025-07-11 22:30:00', 9.0),
  (TIMESTAMP '2025-07-12 12:00:00', 1.0);
"""

_DRY_MONTH = """
CREATE TABLE outflow (timestamp TIMESTAMP, Kies_Efflux DOUBLE);
INSERT INTO outflow VALUES
  (TIMESTAMP '2025-07-10 12:00:00', 0.0),
  (TIMESTAMP '2025-07-11 12:00:00', 0.0);
"""


def test_t04_reads_a_day_of_zeroes_as_a_no(tmp_path: Path):
    """Zero outflow is the "no" class, and it is most of the record.

    Outflow is exactly zero on 75-94 % of band days depending on the roof
    (``findings.md``), so this is the half of T04's balance that the rejection
    sampler has to *keep* — reading it as a missing day would delete it.
    """
    ctx = _context(
        as_of=datetime(2026, 1, 1, 12, 0, tzinfo=BERLIN),
        db=_executor(tmp_path / "t04dry.duckdb", _DRY_MONTH),
    )

    answer = asyncio.run(
        t04_outflow_occurred(_inputs("T04", roof="gravel", date="2025-07-10"), ctx)
    )

    assert answer.answer is False
    assert answer.unit is None
    assert answer.detail["rows"] == 1


def test_t04_reads_a_day_with_no_rows_as_a_defect(tmp_path: Path):
    """A day of nothing is not a "no" — it is a draw the coverage filter missed.

    The two are one boolean apart and a null sum read as ``False`` would hide
    the second inside the answer distribution the balance rule is tuned against.
    """
    ctx = _context(
        as_of=datetime(2026, 1, 1, 12, 0, tzinfo=BERLIN),
        db=_executor(tmp_path / "t04gap.duckdb", _DRY_MONTH),
    )

    with pytest.raises(OracleInputError, match="no reading"):
        asyncio.run(
            t04_outflow_occurred(_inputs("T04", roof="gravel", date="2025-07-20"), ctx)
        )


def test_t04_on_the_pinned_database_answers_both_ways():
    """16.1 L on 2025-07-15 and exactly 0.0 over the 48 rows of 2025-07-01.

    Both classes off one roof and one month, so the template is checked to be
    answerable in both directions on the record it will be sampled from.
    """
    ctx = make_case_context(AS_OF)

    wet = asyncio.run(
        t04_outflow_occurred(_inputs("T04", roof="Kiesdach", date="2025-07-15"), ctx)
    )
    dry = asyncio.run(
        t04_outflow_occurred(_inputs("T04", roof="Kiesdach", date="2025-07-01"), ctx)
    )

    assert (wet.answer, wet.detail["daily_total_l"]) == (True, 16.1)
    assert (dry.answer, dry.detail["daily_total_l"], dry.detail["rows"]) == (False, 0.0, 48)


def test_t05_takes_the_argmax_over_the_sites_own_days(tmp_path: Path):
    """2025-07-12, because the 22:30 UTC row belongs to the Berlin 12th.

    Grouped by UTC day the same three rows peak on the 11th. This is the one
    template where the day boundary changes the *answer* rather than a digit of
    it (``decisions.md`` § The day boundary).
    """
    ctx = _context(
        as_of=datetime(2026, 1, 1, 12, 0, tzinfo=BERLIN),
        db=_executor(tmp_path / "t05.duckdb", _PEAK_ROWS),
    )

    answer = asyncio.run(
        t05_peak_outflow_day(_inputs("T05", roof="gravel", month="2025-07"), ctx)
    )

    assert answer.answer == "2025-07-12"
    assert answer.answer != "2025-07-11"
    assert answer.unit is None
    assert answer.detail["peak_total_l"] == 10.0


def test_t05_refuses_a_month_whose_peak_is_tied(tmp_path: Path):
    """Two days sharing the maximum give the question two defensible answers.

    The refusal also covers the case that would otherwise be silently absurd: an
    entirely dry month ties at 0.0 on every day in it, and a broken tie would
    report "the peak was the 1st".
    """
    ctx = _context(
        as_of=datetime(2026, 1, 1, 12, 0, tzinfo=BERLIN),
        db=_executor(tmp_path / "t05tie.duckdb", _DRY_MONTH),
    )

    with pytest.raises(OracleInputError, match="more than one defensible answer"):
        asyncio.run(t05_peak_outflow_day(_inputs("T05", roof="gravel", month="2025-07"), ctx))


def test_t05_on_the_pinned_database():
    """The gravel roof's July 2025 peak is 2025-07-15 at 16.1 L, and it is unique.

    The month's next two days are 12.8 L on the 21st and 11.2 L on the 12th, so
    the argmax has margin and the tie check is not what is being exercised here.
    """
    answer = asyncio.run(
        t05_peak_outflow_day(
            _inputs("T05", roof="gravel", month="2025-07"), make_case_context(AS_OF)
        )
    )

    assert answer.answer == "2025-07-15"
    assert answer.detail["peak_total_l"] == 16.1


def test_t15a_sums_the_station_rain_column_over_the_sites_own_days(tmp_path: Path):
    """1.5 mm on the Berlin 1st, not the 5.5 mm a UTC grouping reports.

    Read off ``_WETTER_ROWS``: 1.0 + 0.5 falls on the Berlin 1st, and the 4.0 at
    22:30 UTC is already the Berlin 2nd. Grouped by the raw column all three land
    on the 1st, which is the shape of the error the shared day expression exists
    to prevent.
    """
    ctx = _context(
        as_of=datetime(2026, 1, 1, 12, 0, tzinfo=BERLIN),
        db=_executor(tmp_path / "t15a.duckdb", _WETTER_ROWS),
    )

    answer = asyncio.run(
        t15a_past_rain(_inputs("T15a", past_period="2025-07-01..2025-07-01"), ctx)
    )

    assert answer.answer == 1.5
    assert answer.answer != 5.5
    assert answer.unit == "mm"


def test_t15a_agrees_with_the_station_weather_path_on_a_covered_window():
    """29.257 mm either way — the equivalence ``decisions.md`` states, measured.

    T15a's gold set is ``{text_to_sql_agent}`` while the weather tool's station
    path derives its ``precip`` from this same column, so a candidate answering
    through the weather tool returns the identical number and loses trajectory
    anyway. That cost is accepted, but it rests on the two numbers actually being
    the same — which is a claim about the derivation and the raw sum, and is
    checked here rather than argued. The window is seven complete days, which is
    the condition under which the station serves at all: it drops any day short
    of its 48 rows and this oracle sums whatever is there.
    """
    ctx = make_case_context(AS_OF)
    window = "2026-04-13..2026-04-19"

    answer = asyncio.run(t15a_past_rain(_inputs("T15a", past_period=window), ctx))
    rows = StationWeatherSource(ctx.db).daily_rows(date(2026, 4, 13), date(2026, 4, 19))

    # 0.0 + 2.159 + 0.068 + 0.0 + 0.0 + 7.684 + 19.346, the record's own days.
    assert answer.answer == pytest.approx(29.257)
    assert len(rows) == 7
    assert round(sum(row.precip for row in rows), 3) == answer.answer


@pytest.mark.parametrize(
    "period", ["2025-07", "2025-07-01..", "last week", "2025-07-31..2025-07-01"]
)
def test_a_period_that_is_not_two_days_fails_at_the_parameter(period: str):
    """A window the oracle cannot read fails before any arithmetic runs."""
    with pytest.raises(OracleInputError):
        period_window(period)


def test_a_period_reaching_past_the_cut_is_refused(tmp_path: Path):
    """The same containment T01 enforces on a month, on a sampled window.

    ``period_param_within_as_of`` does see both ends of a ``start..end`` period,
    so this is belt and braces there — and the belt is what T01 has instead of
    braces, since ``"2025-07"`` carries no day for that check to read.
    """
    ctx = _context(
        as_of=datetime(2025, 7, 15, 12, 0, tzinfo=BERLIN),
        db=_executor(tmp_path / "t15acut.duckdb", _WETTER_ROWS),
    )

    with pytest.raises(OracleInputError, match="past the case's as_of"):
        asyncio.run(
            t15a_past_rain(_inputs("T15a", past_period="2025-07-01..2025-07-31"), ctx)
        )


def test_a_station_column_the_semantic_layer_dropped_is_refused(
    monkeypatch: pytest.MonkeyPatch,
):
    """A column the candidate is not shown is a column no route could have read.

    ``Rain`` and ``Tmax`` are the two names family A writes out rather than
    resolves through ``roofs.py``, because the station belongs to no roof. What
    they are checked against is the schema block the sub-agent is given, which is
    the closest this family gets to the shared core the others have by import.
    """
    monkeypatch.setattr(sql_module, "_declared_columns", lambda _table: frozenset({"Rain"}))

    assert sql_module.station_column("Rain") == "Rain"
    with pytest.raises(OracleInputError, match="no longer declares"):
        sql_module.station_column("Tmax")


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


# --- T10, T19: the rest of family D (T110) -------------------------------------


def test_t10_returns_the_minimum_itself_in_theta(monkeypatch: pytest.MonkeyPatch):
    """T09's number without T09's comparison: 14.29 %θ off a ramp from 28.57.

    Seeded at 20 mm and losing 5 mm a day over three days, the driest day is the
    last at 10 mm — 14.29 %θ on a 7 cm roof — and the answer is that value rather
    than a bool about it.

    **The unit is %θ, not the millimetres GR2L holds the store in.** No template
    asks for millimetres of storage (``questions.md`` §1.5), and 10.0 against
    14.29 is exactly the pair a wrong unit would swap.
    """
    monkeypatch.setattr(gr2l_module, "run_gr2l", StubGr2l(ssub=20.0, step=-5.0))
    ctx = _context(weather=StubWeather())

    answer = asyncio.run(t10_predicted_minimum(_inputs("T10", roof=MODELLABLE, d=3), ctx))

    assert answer.answer == FLAT_SWC_PCT
    assert answer.answer != FLAT_SSUB_MM
    assert answer.unit == "%θ"
    assert answer.detail["driest_day"] == "2026-04-22"


def test_t10_and_t09_read_one_minimum(monkeypatch: pytest.MonkeyPatch):
    """The pair's whole point: one run, one ``min_swc_pct``, two questions about it.

    A candidate that reports the minimum correctly and then compares it wrongly
    fails T09 and passes T10. Two oracles with two notions of "the minimum" could
    not separate that from a candidate that got both wrong.
    """
    monkeypatch.setattr(gr2l_module, "run_gr2l", StubGr2l(ssub=20.0, step=-5.0))
    ctx = _context(weather=StubWeather())

    ten = asyncio.run(t10_predicted_minimum(_inputs("T10", roof=MODELLABLE, d=3), ctx))
    nine = asyncio.run(
        t09_falls_below_threshold(_inputs("T09", roof=MODELLABLE, thr=15.0, d=3), ctx)
    )

    assert ten.answer == nine.detail["min_swc_pct"]
    assert nine.answer is (ten.answer < 15.0)


def test_t10_agrees_with_the_water_balance_tools_summary(monkeypatch: pytest.MonkeyPatch):
    """The answer *is* ``summary.min_swc_pct``, checked against the tool that reports it.

    :func:`~eval.oracles.model_chain.min_swc_pct` re-derives one line rather than
    importing ``_summarize``, which needs the forcing rows back. This is the test
    that keeps the two from drifting.
    """
    monkeypatch.setattr(gr2l_module, "run_gr2l", StubGr2l(ssub=18.0, step=-2.5))
    ctx = _context(weather=StubWeather())

    answer = asyncio.run(t10_predicted_minimum(_inputs("T10", roof=MODELLABLE, d=4), ctx))
    tool = asyncio.run(make_green_roof_balance_tool(ctx)(MODELLABLE, forecast_days=4))

    assert tool["status"] == "success"
    assert answer.answer == tool["summary"]["min_swc_pct"]


def test_t10_refuses_a_roof_the_water_balance_declines(monkeypatch: pytest.MonkeyPatch):
    """Pool P2, read off the tool's own scope table rather than a second list."""
    monkeypatch.setattr(gr2l_module, "run_gr2l", StubGr2l())
    ctx = _context(weather=StubWeather())

    with pytest.raises(OracleInputError, match="P2"):
        asyncio.run(t10_predicted_minimum(_inputs("T10", roof="Kiesdach", d=3), ctx))


# T19's fixture: seven half-hourly `swc` readings, one per day of the window
# 2026-04-13..2026-04-19, each the whole of its Berlin day's mean. Against a GR2L
# series held flat at 14.0 mm — 20.0 %θ on the 7 cm roof — the daily deviations
# are 5, 4, 3, 2, 1, 0 and 1 pp, so the mean is 16 / 7 = 2.2857… → 2.29 and the
# largest is 5.0. Neither is the other, and neither is the last day's.
_T19_MEASURED = (15.0, 16.0, 17.0, 18.0, 19.0, 20.0, 21.0)
_T19_FLAT_MM = 14.0
_T19_FLAT_PCT = round(mm_to_theta_pct(_T19_FLAT_MM, float(ROOF_PRESETS[MODELLABLE]["SH"])), 2)
_T19_MEAN_DEVIATION = 2.29
_T19_MAX_DEVIATION = 5.0


def _t19_db(path: Path, values: tuple[float, ...] = _T19_MEASURED) -> DuckDbQueryExecutor:
    """A ``swc`` table holding one midday reading per day of T19's window."""
    column = ROOFS[MODELLABLE].columns["swc"]
    rows = ", ".join(
        f"(TIMESTAMP '2026-04-{13 + offset:02d} 10:00:00', {value})"
        for offset, value in enumerate(values)
    )
    return _executor(
        path,
        f"CREATE TABLE swc (timestamp TIMESTAMP, {column} DOUBLE);"
        f"INSERT INTO swc VALUES {rows};",
    )


def test_t19_averages_the_absolute_daily_deviation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """2.29 pp, worked out from seven days: |20 − (15…21)| = 5,4,3,2,1,0,1 → 16/7.

    The **mean of the absolute** deviations, which is not the absolute value of
    the mean: the signed deviations are +5, +4, +3, +2, +1, 0, −1 and sum to 14,
    so a signed average would answer 2.0 over the same seven days. The fixture
    crosses zero on purpose, because a series the model is uniformly above cannot
    tell the two apart.
    """
    monkeypatch.setattr(gr2l_module, "run_gr2l", StubGr2l(ssub=_T19_FLAT_MM))
    ctx = _context(db=_t19_db(tmp_path / "t19.duckdb"), weather=StubWeather())

    answer = asyncio.run(t19_model_deviation(_inputs("T19", roof=MODELLABLE, d=7), ctx))

    assert _T19_FLAT_PCT == 20.0
    assert answer.answer == _T19_MEAN_DEVIATION
    assert answer.answer != 2.0
    assert answer.unit == "pp"
    assert answer.detail["max_abs_deviation_pct"] == _T19_MAX_DEVIATION
    assert answer.detail["compared_days"] == 7


def test_t19_asks_for_complete_past_days_ending_yesterday(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """"The last 7 days" at an ``as_of`` of 2026-04-20 is 04-13..04-19, not 04-14..04-20.

    ``resolve_window(past_days=…)`` is the tool's own resolver and it excludes
    today, because today is not a complete day. An oracle that included it would
    compare the model against a partial daily mean and call the difference model
    error.
    """
    monkeypatch.setattr(gr2l_module, "run_gr2l", StubGr2l(ssub=_T19_FLAT_MM))
    weather = StubWeather()
    ctx = _context(db=_t19_db(tmp_path / "t19win.duckdb"), weather=weather)

    answer = asyncio.run(t19_model_deviation(_inputs("T19", roof=MODELLABLE, d=7), ctx))

    assert weather.calls == [("2026-04-13", "2026-04-19")]
    assert answer.detail["window"] == "2026-04-13..2026-04-19"


def test_t19_agrees_with_evaluate_against_measured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """The tool's own ``evaluation``, reached through the same two functions.

    ``daily_mean_swc`` for the measured series and ``_compare_to_measured`` for
    the join: the oracle calls both rather than averaging a difference of its
    own, so the day boundary, the failed-sensor bound and the overlap rule are
    one implementation.
    """
    monkeypatch.setattr(gr2l_module, "run_gr2l", StubGr2l(ssub=_T19_FLAT_MM, step=0.4))
    ctx = _context(db=_t19_db(tmp_path / "t19agree.duckdb"), weather=StubWeather())

    answer = asyncio.run(t19_model_deviation(_inputs("T19", roof=MODELLABLE, d=7), ctx))
    tool = asyncio.run(
        make_green_roof_balance_tool(ctx)(
            MODELLABLE, past_days=7, evaluate_against_measured=True
        )
    )

    assert tool["status"] == "success"
    assert answer.answer == tool["evaluation"]["mean_abs_deviation_pct"]
    assert answer.detail["compared_days"] == tool["evaluation"]["days"]


def test_t19_refuses_a_window_the_record_does_not_cover(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """No overlap is a coverage failure, not a deviation of zero.

    ``_compare_to_measured`` returns ``days: 0`` and a reason, which the tool
    reports honestly and an oracle must not turn into a number. §1.6's coverage
    filter is what should have rejected the draw.

    The fixture holds one reading the day *before* the window, which is what
    separates the two failures: the run is seeded and simulates fine, and it is
    the comparison that has nothing to stand on.
    """
    monkeypatch.setattr(gr2l_module, "run_gr2l", StubGr2l(ssub=_T19_FLAT_MM))
    column = ROOFS[MODELLABLE].columns["swc"]
    ctx = _context(
        db=_executor(
            tmp_path / "t19empty.duckdb",
            f"CREATE TABLE swc (timestamp TIMESTAMP, {column} DOUBLE);"
            f"INSERT INTO swc VALUES (TIMESTAMP '2026-04-12 10:00:00', 20.0);",
        ),
        weather=StubWeather(),
    )

    with pytest.raises(OracleInputError, match="nothing to compare"):
        asyncio.run(t19_model_deviation(_inputs("T19", roof=MODELLABLE, d=7), ctx))


def test_t19_stamps_the_station_derivation_where_the_station_forced_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """The one model template whose window can resolve to the station.

    Every forward window falls to the Archive whole — the as-of view has
    truncated today, so no forecast window is ever complete (family C's
    measurement) — which leaves T19's retrospective week as the only GR2L run in
    the catalog that can be forced by the site's own instruments, and the only
    one whose pins can carry ``station_derivation``.
    """
    monkeypatch.setattr(gr2l_module, "run_gr2l", StubGr2l(ssub=_T19_FLAT_MM))
    ctx = _context(
        db=_t19_db(tmp_path / "t19pins.duckdb"), weather=StubWeather(source="station")
    )

    pins = asyncio.run(t19_model_deviation(_inputs("T19", roof=MODELLABLE, d=7), ctx)).pins

    assert pins["weather_source"] == ["station"]
    assert pins["station_derivation"] == station_derivation_version()
    assert len(pins["gr2l_canary"]) == 64


@pytest.mark.parametrize("days", [0, -3, 2.5, "7", True])
def test_t19_refuses_a_look_back_that_is_not_whole_days(days: object):
    """One rule, two directions: the backward horizon is checked like the forward one."""
    with pytest.raises(OracleInputError, match="whole number of days"):
        past_days_for(days)


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
    schema = json.loads(Path("eval/schema/case.schema.json").read_text(encoding="utf-8"))
    admitted = set(schema["$defs"]["pins"]["properties"])

    assert admitted == {
        "duckdb_sha256",
        "gr2l_canary",
        "station_derivation",
        "weather_source",
    }


# --- T06, T17a, T18a: the coverage draws (T107) --------------------------------


def test_t06_reads_the_dry_threshold_from_the_constants():
    """10.0 %θ, and it comes from `rules_constants`, not from the card.

    The card's `values:` block is test-bound to the constants (§3.2), so reading
    the constant is reading what the card must say — while reading the card would
    make the oracle agree with a drifted card, which is the one disagreement the
    binding exists to catch.
    """
    answer = asyncio.run(t06_stated_constant(_inputs("T06"), _context()))

    assert answer.answer == ROOF_RULES["irrigated_extensive"].dry_pct
    assert answer.answer == 10.0
    assert answer.unit == "%θ"
    assert answer.status == "answered"
    assert answer.detail["level"] == "dry_pct"


def test_t06_refuses_if_the_extensive_roofs_stop_sharing_a_threshold(
    monkeypatch: pytest.MonkeyPatch,
):
    """"The threshold for the extensive roofs" needs there to be one of them.

    Single-valuedness is a property of the constants, not of the question, so it
    is checked rather than assumed: if one roof moved, §1.6's oracle-validity
    filter should discard the template rather than the oracle picking a side.
    """
    moved = dict(ROOF_RULES)
    moved["irrigated_extensive"] = dataclasses.replace(
        ROOF_RULES["irrigated_extensive"], dry_pct=12.0
    )
    monkeypatch.setattr(reference_module, "ROOF_RULES", moved)

    with pytest.raises(OracleInputError, match="no longer share"):
        asyncio.run(t06_stated_constant(_inputs("T06"), _context()))


def test_t17a_abstains_and_grounds_it_in_the_rule_parameters():
    """No wind anywhere in the ladder's inputs, so the case cannot be answered.

    The check is over parameters and never over prose: the card's text *names*
    wind, among the quantities the rule does not test, so a keyword scan would
    report a wind clause exactly where the card is most explicit that there is
    none.
    """
    answer = asyncio.run(t17a_absent_constant(_inputs("T17a"), _context()))

    assert answer.status == "not_available"
    assert answer.answer is None
    assert answer.unit is None
    assert not [p for p in answer.detail["rule_parameters"] if "wind" in p]


def test_t17a_refuses_if_the_rule_grows_a_wind_parameter(
    monkeypatch: pytest.MonkeyPatch,
):
    """A wind cutoff would make the case answerable and its expectation wrong."""
    card = load_card("irrigation_rule")
    grown = card.model_copy(
        update={"values": {**(card.values or {}), "wind_cutoff_kmh": 30.0}}
    )
    monkeypatch.setattr(reference_module, "load_card", lambda _id: grown)

    with pytest.raises(OracleInputError, match="wind_cutoff_kmh"):
        asyncio.run(t17a_absent_constant(_inputs("T17a"), _context()))


def test_t17b_abstains_on_the_exclusion_the_card_carries():
    """The wetland is under ``not_applicable:``, with the reason it is excluded for.

    ``lookup_reference`` returns a known topic whole and never filters that block
    by roof, so the agent is handed three substrate roofs' thresholds and the
    wetland's exclusion in one object. The oracle's content is that exclusion.
    """
    answer = asyncio.run(t17b_scope_near_miss(_inputs("T17b"), _context()))

    assert answer.status == "not_available"
    assert answer.answer is None
    assert answer.unit is None
    assert answer.detail["roof"] == "wetland"
    assert "fleece mat" in answer.detail["excluded_because"]


def test_t17b_records_the_rule_the_exclusion_sits_under():
    """Three roofs keep a stated threshold, and that is half of what makes this hard.

    The probe is the exclusion sitting *under* a confidently worded rule that
    does not apply, so which roofs still carry one is part of what the answer was
    materialized against and is written into the detail rather than assumed.
    """
    answer = asyncio.run(t17b_scope_near_miss(_inputs("T17b"), _context()))

    assert answer.detail["thresholds_stated_for"] == [
        "irrigated_extensive",
        "non_irrigated_extensive",
        "semi_intensive",
    ]


def test_t17b_refuses_a_card_with_nothing_beside_the_exclusion(
    monkeypatch: pytest.MonkeyPatch,
):
    """An empty ``values:`` block makes the case pass for the wrong reason."""
    card = load_card("irrigation_threshold")
    monkeypatch.setattr(
        reference_module, "load_card", lambda _id: card.model_copy(update={"values": {}})
    )

    with pytest.raises(OracleInputError, match="no longer a near miss"):
        asyncio.run(t17b_scope_near_miss(_inputs("T17b"), _context()))


def test_t17b_refuses_a_roof_that_gained_a_threshold(monkeypatch: pytest.MonkeyPatch):
    """A wetland with both an exclusion and a number is a card mid-edit.

    Either the roof is out of scope or it has a threshold; carrying both makes
    the expectation wrong in a way nothing else in the suite would notice.
    """
    card = load_card("irrigation_threshold")
    grown = card.model_copy(
        update={"values": {**card.values, "wetland": {"dry_pct": 40.0}}}
    )
    monkeypatch.setattr(reference_module, "load_card", lambda _id: grown)

    with pytest.raises(OracleInputError, match="now has a threshold"):
        asyncio.run(t17b_scope_near_miss(_inputs("T17b"), _context()))


def test_t17b_answers_about_the_roof_it_was_asked_about():
    """A sampled roof is read; an unsampled one falls back to the wetland.

    The template names the wetland, and family I is where the two excluded roofs
    are sampled as a set. Reading the parameter when there is one is what stops
    the oracle answering quietly about the wetland if that ever changes.
    """
    named = asyncio.run(t17b_scope_near_miss(_inputs("T17b", roof="Kiesdach"), _context()))
    default = asyncio.run(t17b_scope_near_miss(_inputs("T17b"), _context()))

    assert named.detail["roof"] == "gravel"
    assert named.status == "not_available"
    assert default.detail["roof"] == T17B_ROOF


def test_t17b_refuses_a_roof_the_card_covers():
    """An extensive roof has a threshold, so there is no abstention to score.

    T06 is that question; reaching here with it means the draw went to the wrong
    template rather than that the case is hard.
    """
    with pytest.raises(OracleInputError, match="no longer excludes"):
        asyncio.run(
            t17b_scope_near_miss(_inputs("T17b", roof="irrigated_extensive"), _context())
        )


# --- T13, T14, T15b, T18b: the rest of family C (T110) -------------------------

# One window whose three days differ, so every family C answer is a different
# number and none of them can be produced by reading the wrong day:
#
#   precip  1.2  0.0  4.3   -> total 5.5 mm
#   tx     18.4 22.9 21.0   -> highest 22.9 C, on day 2
#
# `tm` is deliberately higher than `tx` would allow on no day: T14 has to pick
# the daily maximum out of a row that carries the mean beside it, and a stub
# where the two agreed would not test the choice.
_PRECIP = (1.2, 0.0, 4.3)
_TX = (18.4, 22.9, 21.0)

WINDOW_PRECIP_MM = 5.5
WINDOW_HOTTEST_C = 22.9
HOTTEST_DAY = "2026-04-21"


class SeriesWeather:
    """A forecast whose days differ, so an oracle cannot pass by reading day 1."""

    def __init__(self, *, source: str = "station") -> None:
        self.calls: list[tuple[str, str]] = []
        self._source = source

    async def fetch(self, *, start_date: str, end_date: str) -> WeatherResult:
        self.calls.append((start_date, end_date))
        start = date.fromisoformat(start_date)
        span = (date.fromisoformat(end_date) - start).days + 1
        return WeatherResult(
            latitude=51.353484,
            longitude=12.432152,
            timezone="Europe/Berlin",
            source=self._source,
            data=[
                DailyWeatherRow(
                    Date=(start + timedelta(days=offset)).isoformat(),
                    tm=_TX[offset % len(_TX)] - 5.0,
                    tx=_TX[offset % len(_TX)],
                    tn=_TX[offset % len(_TX)] - 9.0,
                    rf=61.0,
                    precip=_PRECIP[offset % len(_PRECIP)],
                    w=7.0,
                    gs=1900.0,
                )
                for offset in range(span)
            ],
        )


def test_t15b_totals_the_forward_windows_rain():
    """5.5 mm = 1.2 + 0.0 + 4.3, over the window ``forecast_days=3`` resolves to.

    The window is checked as well as the total: "the next 3 days" is today and
    the two after it, and both sides go through ``resolve_window`` so the window
    a case is scored on and the window it was answered over are one resolution.
    """
    weather = SeriesWeather()
    ctx = _context(weather=weather)

    answer = asyncio.run(t15b_future_rain(_inputs("T15b", d=3), ctx))

    assert answer.answer == WINDOW_PRECIP_MM
    assert answer.unit == "mm"
    assert weather.calls == [("2026-04-20", "2026-04-22")]
    assert answer.detail["days"] == 3


@pytest.mark.parametrize(
    ("threshold", "expected"),
    [(5.0, True), (WINDOW_PRECIP_MM, False), (6.0, False)],
)
def test_t13_compares_the_windows_total_with_the_threshold(
    threshold: float, expected: bool
):
    """5.5 mm: more than 5.0, not more than 5.5, not more than 6.0.

    The middle case pins the comparison as **strict** — "more than" a threshold
    the window sits exactly on is ``False``. It also pins the quantity as the
    window's *total* rather than any day's: no single day of the three carries
    5.5 mm, so an oracle reading a per-day maximum answers the first case wrong.
    """
    ctx = _context(weather=SeriesWeather())

    answer = asyncio.run(t13_rain_expected(_inputs("T13", thr=threshold, d=3), ctx))

    assert answer.answer is expected
    assert answer.unit is None
    assert answer.detail["total_precip_mm"] == WINDOW_PRECIP_MM


def test_t14_takes_the_highest_daily_maximum_and_not_the_highest_mean():
    """22.9 °C on the 21st — ``tx``, with ``tm`` sitting 5 °C below it in every row.

    The row carries both, so choosing between them is the whole of this oracle's
    content: a question about how hot it will get asks about the peak, and the
    day's average is a different number that is also in front of the candidate.
    """
    ctx = _context(weather=SeriesWeather())

    answer = asyncio.run(t14_forecast_max_temperature(_inputs("T14", d=3), ctx))

    assert answer.answer == WINDOW_HOTTEST_C
    assert answer.unit == "°C"
    assert answer.detail["hottest_day"] == HOTTEST_DAY
    assert answer.detail["field"] == "tx"


def test_family_c_agrees_with_the_weather_tool_on_the_same_context():
    """The claim family C rests on, against the tool its gold trajectory names.

    T13, T14 and T15b all read one window of rows, and the tool returns the same
    rows over the same window — so the three answers are recomputable from the
    tool's own payload. An oracle that resolved a different window, or read a
    different field, would encode as truth a number the gold route cannot
    produce.
    """
    ctx = _context(weather=SeriesWeather())

    rain = asyncio.run(t15b_future_rain(_inputs("T15b", d=3), ctx))
    hottest = asyncio.run(t14_forecast_max_temperature(_inputs("T14", d=3), ctx))
    expected = asyncio.run(t13_rain_expected(_inputs("T13", thr=5.0, d=3), ctx))
    tool = asyncio.run(make_weather_forecast_tool(ctx)(forecast_days=3))

    assert tool["status"] == "success"
    rows = tool["data"]
    assert rain.answer == round(sum(row["precip"] for row in rows), 3)
    assert hottest.answer == max(row["tx"] for row in rows)
    assert expected.answer is (sum(row["precip"] for row in rows) > 5.0)


@pytest.mark.parametrize(
    "oracle", [t13_rain_expected, t14_forecast_max_temperature, t15b_future_rain]
)
def test_family_c_refuses_a_window_past_the_horizon(oracle: Any):
    """Past 16 days the tool abstains, so the case is T18a's and not this one.

    A number computed over that window would be a number the gold route refuses
    to produce, and the abstention metric would then be scoring a template that
    was never meant to reach it.
    """
    weather = SeriesWeather()
    ctx = _context(weather=weather)

    with pytest.raises(OracleInputError, match="T18a"):
        asyncio.run(
            oracle(_inputs("T13", thr=1.0, d=FORECAST_HORIZON_DAYS + 2), ctx)
        )
    assert weather.calls == []


def test_t18b_abstains_without_fetching_anything():
    """Soil temperature is not one of the seven fields, and no call is made.

    The gold trajectory is empty: the tool takes no variable argument, so asking
    it returns the same seven fields it always returns and nothing types a
    ``not_available``. The correct behaviour is to decline *before* calling, and
    an oracle that fetched would be the only thing in the case that did.
    """
    weather = SeriesWeather()
    ctx = _context(weather=weather)

    answer = asyncio.run(
        t18b_missing_variable(_inputs("T18b", variable="soil temperature", d=3), ctx)
    )

    assert answer.status == "not_available"
    assert answer.answer is None
    assert weather.calls == []
    assert answer.detail["fields_served"] == ["gs", "precip", "rf", "tm", "tn", "tx", "w"]


@pytest.mark.parametrize(
    "variable", ["tx", "max temperature", "temperature", "precipitation", "wind speed"]
)
def test_t18b_refuses_a_variable_the_row_actually_carries(variable: str):
    """A quantity the tool serves would record a false abstention as truth.

    The one error here no metric could catch: the false-abstention rate is
    computed against the gold set, so a gold set that calls an answerable
    question unanswerable makes the metric agree with the mistake. "Temperature"
    is in the list because the row carries three of them — the question is
    answerable under any of them, so the draw is not a missing variable.
    """
    ctx = _context(weather=SeriesWeather())

    with pytest.raises(OracleInputError, match="carries"):
        asyncio.run(t18b_missing_variable(_inputs("T18b", variable=variable, d=3), ctx))


@pytest.mark.parametrize("variable", ["maximum temperature", "rain", "sunshine"])
def test_t18b_lets_a_synonym_outside_the_descriptions_through(variable: str):
    """The guard is word containment, not a thesaurus, and it says so.

    "Max" is what the row's description writes, so "maximum temperature" is not
    caught; neither is "rain" for ``precip``. That is why T18b's ``{variable}``
    pool is authored rather than sampled over any noun — the guard catches a pool
    drifting onto the row's own vocabulary, which is how the template would
    decay, and does not pretend to catch a paraphrase of it.
    """
    ctx = _context(weather=SeriesWeather())

    answer = asyncio.run(
        t18b_missing_variable(_inputs("T18b", variable=variable, d=3), ctx)
    )

    assert answer.status == "not_available"


def test_t18b_refuses_a_window_that_would_make_it_t18a():
    """Past the horizon the tool abstains on the *window*, so the variable is moot.

    The case would be T18a wearing T18b's words, and a candidate could pass it
    without ever noticing that soil temperature is not on offer — which is the
    only thing this template tests.
    """
    ctx = _context(weather=SeriesWeather())

    with pytest.raises(OracleInputError, match="T18a"):
        asyncio.run(
            t18b_missing_variable(
                _inputs("T18b", variable="soil temperature", d=FORECAST_HORIZON_DAYS + 2),
                ctx,
            )
        )


def test_t18b_reads_the_field_list_off_the_row_and_not_off_the_docstring():
    """The docstring is candidate-owned, so it cannot be ground truth for anything.

    A candidate that deleted the weather tool's variable list would otherwise
    move T18b's answer — the template would start passing for a reason the
    optimizer had manufactured.
    """
    served = daily_row_fields()

    assert set(served) == set(DailyWeatherRow.model_fields) - {"Date"}
    assert served["tx"] == DailyWeatherRow.model_fields["tx"].description


def test_family_c_stamps_the_source_the_window_resolved_to():
    """A forward window that the station answered stamps the derivation with it.

    ``ctx.weather`` picks the source from the window alone, and the pin records
    which one answered — an oracle calling ``fetch_daily_weather`` directly would
    read the Archive every time while claiming whatever it liked.
    """
    station = _context(weather=SeriesWeather(source="station"))
    archive = _context(weather=SeriesWeather(source="archive"))

    from_station = asyncio.run(t15b_future_rain(_inputs("T15b", d=3), station)).pins
    from_archive = asyncio.run(t15b_future_rain(_inputs("T15b", d=3), archive)).pins

    assert from_station["weather_source"] == ["station"]
    assert from_station["station_derivation"] == station_derivation_version()
    assert from_archive["weather_source"] == ["archive"]
    assert "station_derivation" not in from_archive


def test_t18a_abstains_on_a_window_past_the_horizon():
    """Four weeks out is beyond the 16-day limit, and the window is well formed.

    Well-formed matters: a resolvable window refused for reach is a scope limit
    and types `not_available`, where a malformed one would be an
    `invalid_argument` the candidate could fix. Blurring the two is what makes
    the false-abstention rate unreadable.
    """
    answer = asyncio.run(
        t18a_unservable_window(_inputs("T18a", ahead_days=28), _context())
    )

    assert answer.status == "not_available"
    assert answer.answer is None
    assert answer.detail["horizon_days"] == FORECAST_HORIZON_DAYS
    assert answer.detail["days_past_horizon"] == 28 - FORECAST_HORIZON_DAYS


def test_t18a_refuses_a_window_the_tool_would_actually_serve():
    """Inside the horizon there is no abstention to score, so the draw is invalid."""
    with pytest.raises(OracleInputError, match="within"):
        asyncio.run(
            t18a_unservable_window(
                _inputs("T18a", ahead_days=FORECAST_HORIZON_DAYS - 1), _context()
            )
        )


# --- T24a, T24b: family H (T110) -----------------------------------------------


class RefusesEverything:
    """A database and a weather client that raise if anything asks them for data.

    T24a's oracle resolves a vocabulary and a roof table, and issues no query, no
    weather fetch and no GR2L request — which is a property of
    ``prepare_series`` rather than a rule the oracle keeps, and is what makes the
    declined variant cost nothing at capture. Asserting it needs a seam that
    cannot be read from rather than a count of calls that were not made.
    """

    def execute_query(self, query: str) -> Any:
        raise AssertionError(f"T24a's oracle read the database: {query}")

    async def fetch(self, *, start_date: str, end_date: str) -> Any:
        raise AssertionError(f"T24a's oracle fetched weather for {start_date}..{end_date}")


def _sealed_context(as_of: datetime = AS_OF) -> ScenarioContext:
    seal = RefusesEverything()
    return ScenarioContext.bound(clock=lambda: as_of, db=seal, weather=seal, cache=None)


def test_t24a_answers_a_measured_pair_that_includes_a_non_modellable_roof():
    """The gravel roof's *measured* series is valid, so the pair is drawable.

    This is variant (iii)'s counter-probe, and it is the reason each split's
    ``swc`` pair pins one of the two non-modellable roofs into it: a candidate
    that generalized "gravel ⇒ ``not_available``" is charged for it through the
    false-abstention rate. Inside one family and one tool, the two roofs are
    separated by the series' **source** and by nothing else.
    """
    answer = asyncio.run(
        t24a_plot_request(
            _inputs(
                "T24a",
                variant="measured_pair",
                roof_a="Kiesdach",
                roof_b="irrigated_extensive",
                table="swc",
                month="2025-07",
            ),
            _sealed_context(),
        )
    )

    assert answer.status == "answered"
    assert answer.answer is None
    assert answer.unit is None
    assert [series["source"] for series in answer.detail["series"]] == ["measured"] * 2
    assert [series["roof"] for series in answer.detail["series"]] == [
        "gravel",
        "irrigated_extensive",
    ]


@pytest.mark.parametrize("alias", ["Kiesdach", "Sumpfdach", "gravel", "wetland"])
def test_t24a_declines_a_model_series_for_a_roof_outside_the_model(alias: str):
    """§3.6's own trigger, reached by calling it rather than by restating it.

    The reason comes back in the tool's own words, because the oracle ran the
    tool's own resolver — a membership test written here would have made the
    oracle a second opinion about the plot tool's scope instead of a reading of
    it.
    """
    answer = asyncio.run(
        t24a_plot_request(
            _inputs(
                "T24a", variant="non_modellable_overlay", alias=alias, month="2026-02"
            ),
            _sealed_context(),
        )
    )

    assert answer.status == "not_available"
    assert answer.answer is None
    assert "water-balance model" in answer.detail["declined_because"]


@pytest.mark.parametrize(
    ("variant", "roof"),
    [("model_overlay", "Kiesdach"), ("non_modellable_overlay", "non_irrigated_extensive")],
)
def test_t24a_refuses_a_variant_the_trigger_disagrees_with(variant: str, roof: str):
    """A mislabelled variant is silent in both directions, so it is checked.

    A (ii) draw on a non-modellable roof would record an abstention as answerable
    and a (iii) draw on a modellable one would do the reverse — and the second is
    the error the false-abstention rate cannot see, because the rate is computed
    against the gold set that contains it.
    """
    with pytest.raises(OracleInputError, match="resolves the other way"):
        asyncio.run(
            t24a_plot_request(
                _inputs("T24a", variant=variant, roof=roof, alias=roof, month="2026-02"),
                _sealed_context(),
            )
        )


@pytest.mark.parametrize(
    ("table", "variable"), [("swc", "soil_moisture"), ("outflow", "outflow")]
)
def test_t24a_derives_the_pairs_variable_from_its_table(table: str, variable: str):
    """One table, one per-roof quantity, read off §3.6's closed vocabulary.

    The ``outflow`` draw is what makes §1.8's P1f row real for family H, and it
    exercises the other derived operator: a flux sums where a state averages, on
    the same vocabulary.
    """
    answer = asyncio.run(
        t24a_plot_request(
            _inputs(
                "T24a",
                variant="measured_pair",
                roof_a="gravel",
                roof_b="wetland",
                table=table,
                month="2025-07",
            ),
            _sealed_context(),
        )
    )

    assert {series["variable"] for series in answer.detail["series"]} == {variable}
    assert pair_variable(table) == variable


def test_t24a_refuses_a_pair_that_is_one_roof_twice():
    """"Kiesdach" and "gravel" are one roof, so the pair is compared as segments."""
    with pytest.raises(OracleInputError, match="the request compares two roofs"):
        asyncio.run(
            t24a_plot_request(
                _inputs(
                    "T24a",
                    variant="measured_pair",
                    roof_a="Kiesdach",
                    roof_b="gravel",
                    table="swc",
                    month="2025-07",
                ),
                _sealed_context(),
            )
        )


def test_t24a_refuses_an_undrawable_plot_rather_than_calling_it_an_abstention():
    """The semi-intensive roof has no lysimeter, which is an argument fault.

    The tool types that ``invalid_argument`` — a fumble a candidate could correct
    — where the one thing it types ``not_available`` is a ``model`` series for
    the gravel roof or the wetland. Folding the first into the second is what
    makes the false-abstention rate uninterpretable.
    """
    with pytest.raises(OracleInputError, match="invalid_argument"):
        asyncio.run(
            t24a_plot_request(
                _inputs(
                    "T24a",
                    variant="measured_pair",
                    roof_a="semi_intensive",
                    roof_b="gravel",
                    table="outflow",
                    month="2025-07",
                ),
                _sealed_context(),
            )
        )


def test_t24a_reproduces_the_committed_pilot_cases():
    """The two T24a cases T107 measured over, materialized again by the oracle.

    They were hand-instantiated through the stopgap generator's ``oracle: False``
    branch, which copied the status off the template. The oracle now derives it
    from §3.6 instead, and it has to land on the same four keys — otherwise the
    pilot's numbers were taken against a status this packet has just changed.
    """
    cases = json.loads(Path("eval/cases/pilot.json").read_text(encoding="utf-8"))
    drawn = [case for case in cases if case["inputs"]["template_id"] == "T24a"]

    assert len(drawn) == 2
    for case in drawn:
        ctx = make_case_context(datetime.fromisoformat(case["inputs"]["as_of"]))
        materialized = asyncio.run(t24a_plot_request(case["inputs"], ctx)).expectations()

        assert materialized == {
            key: case["expectations"][key] for key in ("status", "answer", "unit", "pins")
        }


def test_t24a_refuses_a_month_the_case_cannot_see():
    """The measured half has to exist on every variant, so the month is complete."""
    with pytest.raises(OracleInputError, match="past the case's as_of"):
        asyncio.run(
            t24a_plot_request(
                _inputs(
                    "T24a", variant="model_overlay", roof=MODELLABLE, month="2026-04"
                ),
                _sealed_context(),
            )
        )


def test_t24b_answers_t03s_question_through_t03s_arithmetic():
    """The twin pairs two *shapes*, so the two oracles must mean one difference.

    T24b is the measured two-roof comparison with the presentation verb removed,
    and if the scalar were computed a second way the pair would be compared on a
    difference neither template intends.
    """
    ctx = make_case_context(AS_OF)

    scalar = asyncio.run(t24b_extensive_gap(_inputs("T24b", month="2025-07"), ctx))
    twin = asyncio.run(
        t03_irrigation_swc_gap(_inputs("T03", period="2025-07-01..2025-07-31"), ctx)
    )

    assert scalar.answer == twin.answer == pytest.approx(8.377)
    assert scalar.unit == twin.unit == "pp"
    assert scalar.detail["paired_rows"] == twin.detail["paired_rows"]


# --- The registry -------------------------------------------------------------


def test_the_registry_holds_what_has_been_checked_and_only_it():
    """The pilot set plus the families T110 has landed, and nothing on credit.

    Registering an oracle no test exercises would put an unchecked answer one
    lookup away from a case file, and T111 looks a template up here rather than
    mapping an id to a function by parsing it.

    **T24a is registered and materializes a status rather than an answer.** Its
    answer is null on every variant, but §6.1 gives ``status`` no channel but the
    oracle's and the three variants do not agree on it.
    """
    assert sorted(ORACLES) == [
        "T01",
        "T02",
        "T03",
        "T04",
        "T05",
        "T06",
        "T07",
        "T09",
        "T10",
        "T13",
        "T14",
        "T15a",
        "T15b",
        "T17a",
        "T17b",
        "T18a",
        "T18b",
        "T19",
        "T24a",
        "T24b",
    ]
    assert ORACLES["T01"] is t01_total_outflow
    assert ORACLES["T02"] is t02_hot_day_count
    assert ORACLES["T03"] is t03_irrigation_swc_gap
    assert ORACLES["T04"] is t04_outflow_occurred
    assert ORACLES["T05"] is t05_peak_outflow_day
    assert ORACLES["T06"] is t06_stated_constant
    assert ORACLES["T07"] is t07_needs_irrigation_now
    assert ORACLES["T09"] is t09_falls_below_threshold
    assert ORACLES["T10"] is t10_predicted_minimum
    assert ORACLES["T19"] is t19_model_deviation
    assert ORACLES["T15a"] is t15a_past_rain
    assert ORACLES["T17a"] is t17a_absent_constant
    assert ORACLES["T17b"] is t17b_scope_near_miss
    assert ORACLES["T13"] is t13_rain_expected
    assert ORACLES["T14"] is t14_forecast_max_temperature
    assert ORACLES["T15b"] is t15b_future_rain
    assert ORACLES["T18a"] is t18a_unservable_window
    assert ORACLES["T18b"] is t18b_missing_variable
    assert ORACLES["T24a"] is t24a_plot_request
    assert ORACLES["T24b"] is t24b_extensive_gap
