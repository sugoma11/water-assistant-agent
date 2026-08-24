"""Oracles for family A — the pure-SQL templates (``questions.md`` §2 A).

These are the one family with **no shared core to import**. The tool under test
is a language model writing DuckDB, so there is no ``run_*`` function whose
result the oracle could take; the oracle writes its own query and the two agree
only if they mean the same thing by the question. Everything that could make
them mean different things is therefore taken from one place rather than
retyped:

* the roof's column comes from ``roofs.py`` (principle 4), never from a literal;
* the day boundary comes from ``site_day_expr()`` — the same expression the
  semantic layer hands the model in its schema block, so the candidate is told
  to group the way the oracle groups (``decisions.md`` § The day boundary);
* the rows come through ``ctx.db``, the case's as-of executor, so the oracle
  cannot see a row the candidate could not.

**The two ``wetter`` columns are the exception, and they are checked rather than
retyped.** The station belongs to no roof, so ``roofs.py`` has no column for it.
``Rain`` is taken from the plot tool's closed vocabulary — shared tool code, so
T15a and a ``measured`` precipitation series read one name. ``Tmax`` is in no
shared constant at all: the only other place it appears is the station
derivation, inside a private SQL fragment, and family A must *not* borrow that
fragment because the derivation drops incomplete days and a candidate writing
SQL over ``wetter`` does not (:func:`station_column` says what is done instead).

**What is not the oracle's job here.** §1.6's coverage filter — a period
aggregate wants ≥95 % of its expected rows — belongs to the generator, and
re-running it here would turn a badly sampled case into a wrong answer instead
of a rejected draw. What these oracles do refuse is a question with more than
one defensible reading: a month still running at ``as_of``, a peak day that is
tied, a window whose rows are simply absent.

T01 came with T103's pilot set; T02–T05 and T15a are T110's, and
:func:`mean_swc_gap` is shared with family H's T24b, which asks T03's question
with a presentation verb removed rather than added.
"""

from __future__ import annotations

import asyncio
import calendar
import re
from collections.abc import Mapping
from datetime import date
from functools import lru_cache
from typing import Any

from water_assistant_agent.assistant.context import ScenarioContext
from water_assistant_agent.assistant.tools.plot import MEASURED_VOCABULARY
from water_assistant_agent.assistant.tools.roofs import ROOFS, RoofSegment, resolve_roof
from water_assistant_agent.assistant.tools.site import site_day_expr
from water_assistant_agent.tenants.green_roof.sensordata import table_schema_dict

from eval.oracles.base import OracleAnswer, OracleInputError, required_params
from eval.oracles.pins import stamp
from harness.assertions import site_day

_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

RANGE_SEPARATOR = ".."
"""How a ``{period}`` parameter writes its two ends — ``harness.assertions``' own.

The period templates carry an explicit ``start..end`` because that is the form
``_days_in`` reads days out of, so ``period_param_within_as_of`` sees both ends
of a sampled window rather than passing over it the way it passes over
``"2025-07"`` (:func:`t01_total_outflow`).
"""

EXTENSIVE_PAIR = ("irrigated_extensive", "non_irrigated_extensive")
"""The two roofs T03 and T24b compare, in the order their difference is signed.

Irrigated minus non-irrigated, so the gap is positive while irrigation is doing
something — which is the direction the question's own wording implies and the
one a reader checks a sign against.
"""


def month_window(month: str) -> tuple[date, date]:
    """``"2025-07"`` → the month's first and last day.

    A month is a pair of Berlin calendar days like everything else here; the
    conversion lives in one function so the oracle and the generator's coverage
    filter cannot disagree about whether July has 31 days.
    """
    if not isinstance(month, str) or not _MONTH.match(month):
        raise OracleInputError(f"month must be written YYYY-MM, got {month!r}.")
    year, index = (int(part) for part in month.split("-"))
    return date(year, index, 1), date(year, index, calendar.monthrange(year, index)[1])


def period_window(period: str) -> tuple[date, date]:
    """``"2026-04-13..2026-04-19"`` → the two Berlin days it names, inclusive.

    The other half of :func:`month_window`: a ``{period}`` parameter is written
    out as both of its ends rather than as a phrase, so the window a case was
    answered over is legible in the case file and both ends are visible to
    ``period_param_within_as_of``. A single day is accepted as itself, which is
    what makes the same parser serve T04's ``{date}``.
    """
    if not isinstance(period, str):
        raise OracleInputError(
            f"period must be written YYYY-MM-DD{RANGE_SEPARATOR}YYYY-MM-DD, got {period!r}."
        )
    ends = period.split(RANGE_SEPARATOR)
    if len(ends) > 2 or not all(_DAY.match(part.strip()) for part in ends):
        raise OracleInputError(
            f"period must be written YYYY-MM-DD{RANGE_SEPARATOR}YYYY-MM-DD, got {period!r}."
        )
    try:
        days = [date.fromisoformat(part.strip()) for part in ends]
    except ValueError as exc:
        raise OracleInputError(f"period {period!r} names a day that does not exist: {exc}.") from None
    start, end = (days[0], days[-1])
    if start > end:
        raise OracleInputError(f"period {period!r} ends before it starts.")
    return start, end


def within_as_of(ctx: ScenarioContext, end: date, described: str) -> date:
    """Assert *end* is a day this case can see, and return the case's cut.

    The cut comes from the **context** rather than from ``inputs["as_of"]``: the
    context is what the answer is actually computed through — its executor is the
    as-of view that decides which rows a query can see — so reading the stamp
    instead would let the containment check pass against one date while the query
    ran against another.

    ``harness/assertions.py`` intersects every *day* a parameter carries with the
    cut, which covers a ``{date}`` and a ``start..end`` ``{period}`` and passes
    silently over ``"2025-07"``. This check therefore matters most on the month
    templates and is applied to all of them alike, because a window that ends
    past the cut is an ambiguous oracle either way: a "July total" answered with
    half of July is not a hard case, it is a question with two defensible answers,
    which is what §1.6's oracle-validity filter discards.
    """
    cut = site_day(ctx.as_of)
    if end > cut:
        raise OracleInputError(
            f"{described} ends {end.isoformat()}, past the case's as_of "
            f"({cut.isoformat()}); a partial window has no single defensible answer."
        )
    return cut


@lru_cache(maxsize=8)
def _declared_columns(table: str) -> frozenset[str]:
    """The columns the semantic layer declares for *table*.

    The schema block is what the sub-agent is shown, so it is the surface the
    candidate writes SQL against; checking a column against it is the closest
    family A gets to the shared-core property the other families have by import.
    """
    for entry in table_schema_dict:
        if entry["table_name"] == table:
            return frozenset(column["name"] for column in entry["columns"])
    return frozenset()


def station_column(column: str) -> str:
    """*column* of ``wetter``, checked against the semantic layer before it is used.

    The station belongs to no roof, so :mod:`roofs` carries none of its columns
    and this is the one place a name is written rather than resolved. What is
    available to check it against is the schema block the candidate is given, and
    a name absent from that block is a name no route could have read — a testbed
    defect rather than a wrong answer, so it raises.

    **Not borrowed from the station derivation, deliberately.**
    ``weather_station`` aggregates the same two columns, but it serves only days
    carrying all 48 rows and drops the rest (``ROWS_PER_COMPLETE_DAY``). That
    rule belongs to a *weather source* — family C reads it, through
    ``ctx.weather`` — and a candidate answering T02 or T15a with SQL over
    ``wetter`` applies nothing of the kind. Importing the derivation here would
    make the oracle answer a question the gold trajectory does not ask.
    """
    if column not in _declared_columns("wetter"):
        declared = ", ".join(sorted(_declared_columns("wetter"))) or "none"
        raise OracleInputError(
            f"the semantic layer no longer declares a {column!r} column on wetter, so "
            f"no candidate could read it. Declared there: {declared}."
        )
    return column


STATION_RAIN = str(MEASURED_VOCABULARY[("wetter", "precipitation")].station_column)
"""``Rain`` — from the plot tool's closed vocabulary, which already names it once.

Shared tool code, so T15a's total and a ``measured`` precipitation series are
read off one column by construction (``agent_architecture.md`` §3.6).
"""

STATION_AIR_TEMPERATURE_MAX = "Tmax"
"""``Tmax`` — the day's highest air temperature, and the one name with no shared
constant behind it. Guarded by :func:`station_column` at every call site."""


_INSTRUMENT = {"outflow": "lysimeter", "radiation": "radiation mast"}
"""What is missing when a table has no column for a roof, in the site's own words.

``roofs.py`` states the absence as structural — the semi-intensive roof has no
lysimeter and no radiation mast — and a message naming the instrument says which
fact the draw ran into, where "not instrumented in outflow" only restates the
lookup that failed.
"""


def roof_column(name: Any, table: str, pool: str) -> tuple[RoofSegment, str]:
    """The segment *name* resolves to and its column in *table*.

    Raises :class:`OracleInputError` naming *pool* where the roof exists but the
    table has no column for it — the semi-intensive roof's absent lysimeter is
    the whole of that case, and it is read off ``roofs.py`` rather than off a
    hand-written pool list that could fall out of step with the instrumentation.
    """
    roof = resolve_roof(str(name))
    if roof is None:
        raise OracleInputError(f"no roof answers to {name!r}.")
    column = roof.columns.get(table)
    if column is None:
        members = ", ".join(n for n, seg in ROOFS.items() if table in seg.columns)
        missing = _INSTRUMENT.get(table, f"column in {table}")
        raise OracleInputError(
            f"the {roof.name} roof has no {missing}, so it has no {table} column. "
            f"The pool here is {pool} ({members})."
        )
    return roof, column


async def _scalar(ctx: ScenarioContext, query: str) -> Any:
    """Run *query* through the case's as-of executor and return its one value."""
    result = await asyncio.to_thread(ctx.db.execute_query, query)
    return result.rows[0][0] if result.rows else None


async def t01_total_outflow(
    inputs: Mapping[str, Any], ctx: ScenarioContext
) -> OracleAnswer:
    """T01 — total outflow of one roof over one calendar month, in litres.

    ``SUM`` over the roof's efflux column across the month's Berlin days. The
    lysimeter collects 1 m², so the litres this returns are numerically the
    millimetres a candidate may answer in, and the answer metric normalizes the
    two rather than scoring one against the other (``findings.md`` § The
    lysimeter collection area, and the semantic layer since T105).

    **The month is required to be complete at ``as_of``**
    (:func:`within_as_of`). ``harness/assertions.py`` intersects every *day* a
    parameter carries with the case's cut, but ``"2025-07"`` carries no day, so
    the check passes silently over the one parameter this template samples — the
    containment is enforced here instead. Without it a case whose ``as_of`` falls
    mid-month would ask for "July's total" and be answered with half of July: not
    a hard case but an ambiguous oracle, which is what §1.6's oracle-validity
    filter discards.

    Params:
        roof: any spelling ``roofs.py`` resolves; pool P1f (has a lysimeter).
        month: ``YYYY-MM``.
    """
    roof_name, month = required_params(inputs, "roof", "month")
    roof, column = roof_column(roof_name, "outflow", "P1f")

    start, end = month_window(str(month))
    within_as_of(ctx, end, f"the month {month}")

    day = site_day_expr()
    query = (
        f"SELECT SUM({column}) FROM outflow "  # noqa: S608 - identifiers from roofs.py, dates are `date`
        f"WHERE {day} BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}'"
    )
    total = await _scalar(ctx, query)
    if total is None:
        raise OracleInputError(
            f"outflow holds no rows for {roof.name} in {month}; the coverage filter "
            "should have rejected this draw (questions.md §1.6)."
        )

    return OracleAnswer(
        answer=round(float(total), 3),
        unit="L",
        pins=stamp(),
        detail={"roof": roof.name, "column": column, "window": f"{start}..{end}"},
    )


async def t02_hot_day_count(
    inputs: Mapping[str, Any], ctx: ScenarioContext
) -> OracleAnswer:
    """T02 — how many days in a period the day's highest air temperature exceeded *thr*.

    Two aggregations, in this order and not the other: the day's maximum first,
    then the count of days over the threshold. Counting half-hourly rows above
    the threshold instead would answer "how many half hours were hot", which is a
    different quantity that happens to be reachable by the same column and is
    what a candidate grouping the wrong way returns.

    **"Exceed" is strict.** A day sitting exactly on the threshold does not
    count. §1.6's oracle-validity filter keeps a draw away from its own boundary,
    so this decides no case that survives generation, but the rule has to be
    written down and this is the place.

    **Incomplete days are counted like any other**, unlike the station
    derivation's (:func:`station_column`): a candidate writing SQL over ``wetter``
    has no completeness rule, and §1.6's coverage predicate is what keeps a
    thinly covered window from being sampled at all.

    Params:
        period: ``YYYY-MM-DD..YYYY-MM-DD``, ending at or before ``as_of``.
        thr: the threshold in °C.
    """
    period, threshold = required_params(inputs, "period", "thr")
    start, end = period_window(str(period))
    within_as_of(ctx, end, f"the period {period}")
    column = station_column(STATION_AIR_TEMPERATURE_MAX)

    day = site_day_expr()
    daily_max = (
        f'SELECT {day} AS day, max("{column}") AS hottest FROM wetter '  # noqa: S608 - identifier checked, dates are `date`
        f"WHERE {day} BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}' "
        f"GROUP BY 1"
    )
    count = await _scalar(
        ctx, f"SELECT count(*) FROM ({daily_max}) WHERE hottest > {float(threshold)}"  # noqa: S608 - subquery built above
    )
    days = await _scalar(ctx, f"SELECT count(*) FROM ({daily_max})")  # noqa: S608 - subquery built above
    if not days:
        raise OracleInputError(
            f"wetter holds no rows over {period}; the coverage filter should have "
            "rejected this draw (questions.md §1.6)."
        )

    return OracleAnswer(
        answer=int(count or 0),
        unit="count",
        pins=stamp(),
        detail={
            "column": column,
            "threshold_c": float(threshold),
            "window": f"{start}..{end}",
            "days_with_rows": int(days),
        },
    )


async def mean_swc_gap(
    ctx: ScenarioContext, start: date, end: date
) -> tuple[float, dict[str, Any]]:
    """The mean %θ gap between the two extensive roofs over ``[start, end]``, in pp.

    T03's core, shared with family H's T24b so the twin pair cannot disagree
    about what "the mean difference" is (``questions.md`` §2 H).

    **The mean of the differences, over rows where both roofs read.** Written as
    ``avg(QEx1 - QEx2)`` rather than as a difference of two averages, which is
    the same number only while the two columns are non-null on exactly the same
    rows — and a sensor out for an afternoon is precisely what makes them differ.
    Requiring both readings is also what makes the quantity a *gap between the
    roofs at an instant*, which is the thing the question names; averaging each
    roof over whatever rows it happened to have compares two different Julys.

    The record is half-hourly, so this is the mean over half-hourly samples and
    not the mean of daily means. The two differ whenever coverage is uneven
    within a day, and the reading chosen is the one the raw column supports with
    no intermediate aggregation to disagree about.
    """
    columns = [ROOFS[name].columns["swc"] for name in EXTENSIVE_PAIR]
    day = site_day_expr()
    both_present = " AND ".join(f'"{column}" IS NOT NULL' for column in columns)
    query = (
        f'SELECT avg("{columns[0]}" - "{columns[1]}"), count(*) FROM swc '  # noqa: S608 - identifiers from roofs.py, dates are `date`
        f"WHERE {day} BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}' "
        f"AND {both_present}"
    )
    result = await asyncio.to_thread(ctx.db.execute_query, query)
    gap, rows = result.rows[0] if result.rows else (None, 0)
    if gap is None or not rows:
        raise OracleInputError(
            f"swc holds no row over {start}..{end} where both "
            f"{' and '.join(EXTENSIVE_PAIR)} read; the coverage filter should have "
            "rejected this draw (questions.md §1.6)."
        )
    detail = {
        "roofs": list(EXTENSIVE_PAIR),
        "columns": columns,
        "window": f"{start}..{end}",
        "paired_rows": int(rows),
    }
    return round(float(gap), 3), detail


async def t03_irrigation_swc_gap(
    inputs: Mapping[str, Any], ctx: ScenarioContext
) -> OracleAnswer:
    """T03 — the mean soil-moisture gap between the two extensive roofs, in pp.

    A difference between two %θ states answers in **pp** and not in %θ
    (``questions.md`` §1.5): the roofs' own states are water contents, the gap
    between them is not, and the unit is what says so.

    The pair is fixed rather than sampled — the question names it — so the only
    parameter is the window. :func:`mean_swc_gap` carries the arithmetic and the
    reading it commits to.

    Params:
        period: ``YYYY-MM-DD..YYYY-MM-DD``, ending at or before ``as_of``.
    """
    (period,) = required_params(inputs, "period")
    start, end = period_window(str(period))
    within_as_of(ctx, end, f"the period {period}")

    gap, detail = await mean_swc_gap(ctx, start, end)
    return OracleAnswer(answer=gap, unit="pp", pins=stamp(), detail=detail)


async def t04_outflow_occurred(
    inputs: Mapping[str, Any], ctx: ScenarioContext
) -> OracleAnswer:
    """T04 — did this roof produce any outflow on this day?

    The daily sum against zero. A recorded past fact, and the template's purpose
    is that a simulation is the epistemically wrong source for one — which is
    what its must-not on the water-balance tool scores (``questions.md`` §2 A).

    **A day of rows that are all zero is a "no", not a missing day**, and the two
    have to stay apart. Outflow is exactly zero on 75–94 % of band days depending
    on the roof (``findings.md``), so the zero class *is* half of this template's
    balance; a day with no rows at all is a coverage failure and raises instead.
    Only the second is a defect, and reading a null sum as ``False`` would hide
    it inside the answer distribution the balance rule is tuned against.

    Params:
        roof: any spelling ``roofs.py`` resolves; pool P1f (has a lysimeter).
        date: ``YYYY-MM-DD``, at or before ``as_of``.
    """
    roof_name, day_param = required_params(inputs, "roof", "date")
    roof, column = roof_column(roof_name, "outflow", "P1f")
    start, end = period_window(str(day_param))
    if start != end:
        raise OracleInputError(f"date names one day, got the range {day_param!r}.")
    within_as_of(ctx, end, f"the day {day_param}")

    day = site_day_expr()
    result = await asyncio.to_thread(
        ctx.db.execute_query,
        f'SELECT sum("{column}"), count("{column}") FROM outflow '  # noqa: S608 - identifiers from roofs.py, dates are `date`
        f"WHERE {day} = DATE '{start.isoformat()}'",
    )
    total, rows = result.rows[0] if result.rows else (None, 0)
    if total is None or not rows:
        raise OracleInputError(
            f"outflow holds no reading for {roof.name} on {start.isoformat()}; the "
            "coverage filter should have rejected this draw (questions.md §1.6). A "
            "day of zeroes is a 'no' and reaches the answer; a day of nothing is this."
        )

    return OracleAnswer(
        answer=bool(float(total) > 0.0),
        unit=None,
        pins=stamp(),
        detail={
            "roof": roof.name,
            "column": column,
            "day": start.isoformat(),
            "daily_total_l": round(float(total), 3),
            "rows": int(rows),
        },
    )


async def t05_peak_outflow_day(
    inputs: Mapping[str, Any], ctx: ScenarioContext
) -> OracleAnswer:
    """T05 — which day of the month this roof's outflow peaked on, as an ISO date.

    The argmax of the daily sums, and the day boundary is what the answer *is*:
    grouping the raw UTC column moves rain between days by an hour or two, which
    on a storm that starts late in the evening moves the argmax by a whole day
    (``decisions.md`` § The day boundary).

    **A tie is refused rather than broken.** Two days sharing the maximum give
    the question two defensible answers, which §1.6's oracle-validity filter
    discards — and the refusal covers the case that would otherwise be silently
    absurd: outflow is zero on most days of most months, so an entirely dry month
    ties at 0.0 across every day in it and would answer "the peak was the 1st".

    Params:
        roof: any spelling ``roofs.py`` resolves; pool P1f (has a lysimeter).
        month: ``YYYY-MM``, complete at ``as_of``.
    """
    roof_name, month = required_params(inputs, "roof", "month")
    roof, column = roof_column(roof_name, "outflow", "P1f")
    start, end = month_window(str(month))
    within_as_of(ctx, end, f"the month {month}")

    day = site_day_expr()
    result = await asyncio.to_thread(
        ctx.db.execute_query,
        f'SELECT {day} AS at, sum("{column}") AS total FROM outflow '  # noqa: S608 - identifiers from roofs.py, dates are `date`
        f"WHERE {day} BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}' "
        # Ordered by ordinal: `at` is DuckDB's `AT TIME ZONE` keyword, and a
        # query ending on the bare word does not parse.
        f'AND "{column}" IS NOT NULL GROUP BY 1 ORDER BY 2 DESC, 1',
    )
    if not result.rows:
        raise OracleInputError(
            f"outflow holds no rows for {roof.name} in {month}; the coverage filter "
            "should have rejected this draw (questions.md §1.6)."
        )
    peak_day, peak_total = result.rows[0]
    tied = [at for at, total in result.rows if total == peak_total]
    if len(tied) > 1:
        raise OracleInputError(
            f"{roof.name}'s outflow in {month} peaks at {peak_total} on "
            f"{len(tied)} days ({', '.join(at.isoformat() for at in tied[:4])}…); "
            "the question has more than one defensible answer (questions.md §1.6)."
        )

    return OracleAnswer(
        answer=peak_day.isoformat(),
        unit=None,
        pins=stamp(),
        detail={
            "roof": roof.name,
            "column": column,
            "peak_total_l": round(float(peak_total), 3),
            "window": f"{start}..{end}",
            "days": len(result.rows),
        },
    )


async def t15a_past_rain(
    inputs: Mapping[str, Any], ctx: ScenarioContext
) -> OracleAnswer:
    """T15a — how much rain fell over a past window, in millimetres.

    The station's own rain column, summed over the window's Berlin days. Scored
    as a pure-SQL positive even though the weather tool's station path derives
    its ``precip`` from this same column and returns the identical number — the
    tense pair with T15b is the discriminating work, and the accepted cost of the
    gold set is recorded under ``decisions.md`` § Trajectory scoring and routing
    probes.

    **This oracle is nevertheless not the station derivation**, and the
    difference is visible exactly where the record is thin: the derivation serves
    only days carrying all 48 rows and this sums whatever rows are there. On a
    fully covered window — which §1.6's coverage filter is what guarantees — the
    two agree to the digit, and the identity above is a claim about those windows
    (:func:`station_column`).

    Params:
        past_period: ``YYYY-MM-DD..YYYY-MM-DD``, ending at or before ``as_of``.
    """
    (period,) = required_params(inputs, "past_period")
    start, end = period_window(str(period))
    within_as_of(ctx, end, f"the period {period}")
    column = station_column(STATION_RAIN)

    day = site_day_expr()
    result = await asyncio.to_thread(
        ctx.db.execute_query,
        f'SELECT sum("{column}"), count("{column}") FROM wetter '  # noqa: S608 - identifier checked, dates are `date`
        f"WHERE {day} BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}'",
    )
    total, rows = result.rows[0] if result.rows else (None, 0)
    if total is None or not rows:
        raise OracleInputError(
            f"wetter holds no rain reading over {period}; the coverage filter should "
            "have rejected this draw (questions.md §1.6)."
        )

    return OracleAnswer(
        answer=round(float(total), 3),
        unit="mm",
        pins=stamp(),
        detail={"column": column, "window": f"{start}..{end}", "rows": int(rows)},
    )
