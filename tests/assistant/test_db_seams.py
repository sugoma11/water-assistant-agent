"""The DB seams and the frozen sub-agent (T035b, packet P1b).

Covers exactly what the packet's exit criterion names: two contexts with
different ``as_of`` running one query concurrently — through the sub-agent path
as well — each on its own bound; the ``CURRENT_DATE`` rewrite pinned; and the
context-bound query tool's name, signature and docstring identical to the
module-level one.

Two conventions, both deliberate. The as-of assertions run against the pinned
``data/water.duckdb`` and are checked against an independent raw query rather
than against a second view, so a test cannot pass by agreeing with the
implementation it is testing. The rewrite assertions run against a spy executor
and read the SQL string it was *handed*: re-emission is the thing T028 adds, and
a test that inspected the parsed tree instead would pass just as happily against
the silent no-op the task exists to remove.
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import pytest

from water_assistant_agent.assistant.agents.text_to_sql.agent import (
    AGENT_DESCRIPTION,
    AGENT_NAME,
    STATIC_INSTRUCTION,
    build_text_to_sql_agent,
)
from water_assistant_agent.assistant.agents.text_to_sql.dry_run import (
    DuckDbExplainValidator,
)
from water_assistant_agent.assistant.context import Clock, ScenarioContext
from water_assistant_agent.assistant.ports import QueryResult
from water_assistant_agent.assistant.tools import warehouse
from water_assistant_agent.assistant.tools.warehouse import make_query_database_tool

DB_PATH = "data/water.duckdb"
BERLIN = ZoneInfo("Europe/Berlin")

# Two bounds inside every table's record, far enough apart that a leak between
# contexts changes the row count by thousands rather than by rounding.
EARLY_AS_OF = datetime(2026, 1, 1, 11, 0, tzinfo=BERLIN)
LATE_AS_OF = datetime(2026, 3, 15, 11, 0, tzinfo=BERLIN)

COUNT_QUERY = "SELECT count(*) AS n FROM outflow"


def _raw_count(as_of: datetime) -> int:
    """``outflow`` rows at or before *as_of*, read straight from the pinned file."""
    con = duckdb.connect(DB_PATH, read_only=True)
    try:
        return con.execute(
            "SELECT count(*) FROM outflow WHERE timestamp <= ?",
            [as_of.astimezone(UTC).replace(tzinfo=None)],
        ).fetchone()[0]
    finally:
        con.close()


def _context(as_of: datetime) -> ScenarioContext:
    """A context frozen at *as_of*; the weather half is out of this packet's scope."""
    return ScenarioContext(
        clock=lambda: as_of,
        db_path=DB_PATH,
        weather_client_factory=lambda db, cache: None,
        http_cache=None,
    )


class SpyExecutor:
    """A ``ReadOnlyWarehouseQuery`` that records the SQL it is handed."""

    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self.queries: list[str] = []
        self._rows = [(1,)] if rows is None else rows

    def execute_query(self, query: str) -> QueryResult:
        self.queries.append(query)
        return QueryResult(columns=("n",), rows=self._rows)


def _tool_for(executor: Any, clock: Clock) -> Any:
    return make_query_database_tool(executor, clock)


async def _run(tool: Any, sql: str) -> dict[str, Any]:
    result = await tool(sql)
    assert result["status"] == "success", result
    return result


# --- Two contexts, one query, each on its own bound ---------------------------


@pytest.mark.asyncio
async def test_two_contexts_query_concurrently_on_their_own_bounds() -> None:
    """The packet's exit criterion, at the executor seam."""
    early, late = _context(EARLY_AS_OF), _context(LATE_AS_OF)
    expected_early, expected_late = _raw_count(EARLY_AS_OF), _raw_count(LATE_AS_OF)
    assert expected_early != expected_late  # otherwise the test proves nothing

    # Interleaved, not merely two calls: a shared connection or a cached bound
    # would show up as one context answering with the other's count.
    results = await asyncio.gather(
        *[
            asyncio.to_thread(ctx.db.execute_query, COUNT_QUERY)
            for _ in range(4)
            for ctx in (early, late)
        ]
    )
    counts = [result.rows[0][0] for result in results]
    assert counts == [expected_early, expected_late] * 4


@pytest.mark.asyncio
async def test_two_sub_agents_query_concurrently_on_their_own_bounds() -> None:
    """The same, through the sub-agent path: each agent's own inner query tool."""
    early, late = _context(EARLY_AS_OF), _context(LATE_AS_OF)
    early_agent = build_text_to_sql_agent(early.db, early.clock)
    late_agent = build_text_to_sql_agent(late.db, late.clock)
    early_tool, late_tool = early_agent.tools[1], late_agent.tools[1]
    assert early_tool.__name__ == late_tool.__name__ == "query_database_tool"

    results = await asyncio.gather(
        *[
            _run(tool, COUNT_QUERY)
            for _ in range(4)
            for tool in (early_tool, late_tool)
        ]
    )
    counts = [result["rows"][0]["n"] for result in results]
    assert counts == [_raw_count(EARLY_AS_OF), _raw_count(LATE_AS_OF)] * 4


@pytest.mark.asyncio
async def test_each_sub_agent_binds_its_own_validator() -> None:
    """T026's seam: the EXPLAIN validator runs on the executor it was given."""
    first, second = SpyExecutor(), SpyExecutor()

    assert await DuckDbExplainValidator(first).avalidate("SELECT 1") is None
    assert await DuckDbExplainValidator(second).avalidate("SELECT 2") is None

    assert first.queries == ["EXPLAIN SELECT 1"]
    assert second.queries == ["EXPLAIN SELECT 2"]


# --- The T028 rewrite ---------------------------------------------------------


@pytest.mark.asyncio
async def test_current_date_is_rewritten_to_the_as_of_literal() -> None:
    """A date node is pinned to ``as_of``'s site-local calendar date."""
    spy = SpyExecutor()
    await _run(
        _tool_for(spy, lambda: EARLY_AS_OF),
        "SELECT count(*) FROM outflow WHERE timestamp >= CURRENT_DATE - INTERVAL 7 DAY",
    )

    executed = spy.queries[0]
    assert "CURRENT_DATE" not in executed.upper()
    assert "CAST('2026-01-01' AS DATE)" in executed


@pytest.mark.asyncio
async def test_the_now_family_is_rewritten_to_the_utc_instant() -> None:
    """Timestamp nodes are pinned in the columns' own frame, naive UTC."""
    spy = SpyExecutor()
    await _run(
        _tool_for(spy, lambda: EARLY_AS_OF),
        "SELECT now(), current_timestamp, today(), get_current_timestamp(), "
        "transaction_timestamp()",
    )

    executed = spy.queries[0]
    # 11:00 Berlin in January is 10:00 UTC; the date node keeps the site's day.
    assert executed.count("CAST('2026-01-01 10:00:00' AS TIMESTAMP)") == 4
    assert "CAST('2026-01-01' AS DATE)" in executed
    for spelling in ("NOW(", "CURRENT_TIMESTAMP", "TODAY(", "TRANSACTION_TIMESTAMP"):
        assert spelling not in executed.upper()


@pytest.mark.asyncio
async def test_the_rewrite_reaches_the_database_not_just_the_parse_tree() -> None:
    """Re-emission, the half T028 actually adds.

    The pass parsed for the read-only guard and then executed the *original*
    string, so this is the assertion that a rewrite is not a silent no-op: what
    the executor is handed must be the rewritten text.
    """
    spy = SpyExecutor()
    original = "select count(*) from outflow where timestamp <= current_date"
    await _run(_tool_for(spy, lambda: EARLY_AS_OF), original)

    assert spy.queries[0] != original
    assert "current_date" not in spy.queries[0].lower()


@pytest.mark.asyncio
async def test_the_rewrite_changes_what_the_as_of_view_returns() -> None:
    """End to end: a dateless query answers about the case's day, not today's.

    Run against the wall clock this window is empty — the record ends well before
    it — so a non-zero count is only reachable if the clock the tool used was the
    context's.
    """
    ctx = _context(EARLY_AS_OF)
    tool = _tool_for(ctx.db, ctx.clock)
    result = await _run(
        tool,
        "SELECT count(*) AS n FROM outflow WHERE timestamp >= CURRENT_DATE - INTERVAL 7 DAY",
    )

    con = duckdb.connect(DB_PATH, read_only=True)
    try:
        expected = con.execute(
            "SELECT count(*) FROM outflow WHERE timestamp >= DATE '2026-01-01' - INTERVAL 7 DAY "
            "AND timestamp <= TIMESTAMP '2026-01-01 10:00:00'"
        ).fetchone()[0]
    finally:
        con.close()

    assert expected > 0
    assert result["rows"][0]["n"] == expected


@pytest.mark.asyncio
async def test_the_clock_is_read_per_call_not_captured() -> None:
    """One tool, a moving clock: the literal follows it without a rebuild."""
    spy = SpyExecutor()
    now = EARLY_AS_OF
    tool = _tool_for(spy, lambda: now)

    await _run(tool, "SELECT CURRENT_DATE")
    now = LATE_AS_OF
    await _run(tool, "SELECT CURRENT_DATE")

    assert "CAST('2026-01-01' AS DATE)" in spy.queries[0]
    assert "CAST('2026-03-15' AS DATE)" in spy.queries[1]


@pytest.mark.asyncio
async def test_the_read_only_guard_survives_the_rewrite() -> None:
    """Re-emission must not become a path around the guard."""
    spy = SpyExecutor()
    result = await _tool_for(spy, lambda: EARLY_AS_OF)("DROP TABLE outflow")

    assert result["status"] == "error"
    assert spy.queries == []


# --- Frozen means the text ----------------------------------------------------


def test_context_bound_query_tool_matches_the_production_default() -> None:
    """Name, signature and docstring — ADK derives the declaration from all three."""
    bound = _tool_for(SpyExecutor(), lambda: EARLY_AS_OF)
    production = warehouse.query_database_tool

    assert bound.__name__ == production.__name__ == "query_database_tool"
    assert bound.__qualname__ == production.__qualname__ == "query_database_tool"
    assert inspect.signature(bound) == inspect.signature(production)
    assert bound.__doc__ == production.__doc__


def test_two_sub_agents_carry_byte_identical_frozen_text() -> None:
    """§3.1: the object is rebuilt per case, the text never changes with it."""
    early, late = _context(EARLY_AS_OF), _context(LATE_AS_OF)
    early_agent = build_text_to_sql_agent(early.db, early.clock)
    late_agent = build_text_to_sql_agent(late.db, late.clock)

    for agent in (early_agent, late_agent):
        # The AgentTool name the root agent sees and trajectory scoring keys on.
        assert agent.name == AGENT_NAME == "text_to_sql_agent"
        assert agent.description == AGENT_DESCRIPTION
        assert agent.static_instruction == STATIC_INSTRUCTION

    assert [tool.__name__ for tool in early_agent.tools] == [
        "query_builder_tool",
        "query_database_tool",
    ]
    assert [tool.__doc__ for tool in early_agent.tools] == [
        tool.__doc__ for tool in late_agent.tools
    ]
    # Rebuilt, not shared: the binding is the only per-case state there is.
    assert early_agent is not late_agent
    assert early_agent.tools[1] is not late_agent.tools[1]


def test_the_candidate_description_is_the_only_text_a_build_may_change() -> None:
    """T029 threads the candidate's text through this argument, and only this one."""
    ctx = _context(EARLY_AS_OF)
    agent = build_text_to_sql_agent(ctx.db, ctx.clock, description="candidate text")

    assert agent.description == "candidate text"
    assert agent.name == AGENT_NAME
    assert agent.static_instruction == STATIC_INSTRUCTION
