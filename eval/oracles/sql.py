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

T01 is the only one implemented here (T103's pilot set); T02–T05 and T15a follow
in T110.
"""

from __future__ import annotations

import asyncio
import calendar
import re
from collections.abc import Mapping
from datetime import date
from typing import Any

from water_assistant_agent.assistant.context import ScenarioContext
from water_assistant_agent.assistant.tools.roofs import ROOFS, resolve_roof
from water_assistant_agent.assistant.tools.site import site_day_expr

from eval.oracles.base import OracleAnswer, OracleInputError, required_params
from eval.oracles.pins import stamp
from harness.assertions import site_day

_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


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


async def t01_total_outflow(
    inputs: Mapping[str, Any], ctx: ScenarioContext
) -> OracleAnswer:
    """T01 — total outflow of one roof over one calendar month, in litres.

    ``SUM`` over the roof's efflux column across the month's Berlin days. The
    lysimeter collects 1 m², so the litres this returns are numerically the
    millimetres a candidate may answer in, and the answer metric normalizes the
    two rather than scoring one against the other (``findings.md`` § The
    lysimeter collection area, and the semantic layer since T105).

    **The month is required to be complete at ``as_of``.** ``harness/
    assertions.py`` intersects every *day* a parameter carries with the case's
    cut, but ``"2025-07"`` carries no day, so the check passes silently over the
    one parameter this template samples — the containment is enforced here
    instead. Without it a case whose ``as_of`` falls mid-month would ask for
    "July's total" and be answered with half of July: not a hard case but an
    ambiguous oracle, which is what §1.6's oracle-validity filter discards.

    Params:
        roof: any spelling ``roofs.py`` resolves; pool P1f (has a lysimeter).
        month: ``YYYY-MM``.
    """
    roof_name, month = required_params(inputs, "roof", "month")
    roof = resolve_roof(str(roof_name))
    if roof is None:
        raise OracleInputError(f"no roof answers to {roof_name!r}.")
    column = roof.columns.get("outflow")
    if column is None:
        pool = ", ".join(name for name, seg in ROOFS.items() if "outflow" in seg.columns)
        raise OracleInputError(
            f"the {roof.name} roof has no lysimeter, so it has no outflow column. "
            f"T01's pool is P1f ({pool})."
        )

    start, end = month_window(str(month))
    # The cut comes from the context, not from `inputs["as_of"]`: the context is
    # what the answer was actually computed through — its executor is the as-of
    # view that decides which rows the query above can even see — so reading the
    # stamp instead would let the containment check pass against one date while
    # the sum ran against another.
    cut = site_day(ctx.as_of)
    if end > cut:
        raise OracleInputError(
            f"{month} ends {end.isoformat()}, past the case's as_of ({cut.isoformat()}); "
            "a partial month has no single defensible total."
        )

    day = site_day_expr()
    query = (
        f"SELECT SUM({column}) FROM outflow "  # noqa: S608 - identifiers from roofs.py, dates are `date`
        f"WHERE {day} BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}'"
    )
    result = await asyncio.to_thread(ctx.db.execute_query, query)
    total = result.rows[0][0] if result.rows else None
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
