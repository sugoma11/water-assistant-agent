"""``ScenarioContext`` / ``connect_asof`` — the as-of injection seam (T035a, packet P1a).

Covers exactly what the packet's exit criterion names: an as-of bound on all
five tables, the cut identical under at least two host ``TZ`` settings, and a
production-clocked context reflecting a date change on its next query with no
explicit rebuild. Runs against the pinned ``data/water.duckdb`` — the same file
every rollout attaches read-only — rather than a synthetic fixture, since the
bound's correctness is a property of the real columns' naive-UTC timestamps.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pytest

from water_assistant_agent.assistant.context import (
    _AS_OF_TABLES,
    AsOfQueryExecutor,
    ScenarioContext,
    connect_asof,
)

DB_PATH = "data/water.duckdb"
BERLIN = ZoneInfo("Europe/Berlin")

# Inside every table's record except radiation's (which ends 2025-10-01), so this
# exercises both a real truncation and a pass-through-in-full case in one pass.
AS_OF = datetime(2026, 1, 1, 12, 0, tzinfo=BERLIN)


def _raw_bound(table: str, as_of_utc_naive: datetime) -> tuple[int, datetime | None]:
    """Row count and max timestamp for *table* at or before *as_of*, read directly
    from the pinned file (never through ``connect_asof``) as the independent oracle
    the view's output is checked against."""
    con = duckdb.connect(DB_PATH, read_only=True)
    try:
        count, max_ts = con.execute(
            f"SELECT count(*), max(timestamp) FROM {table} WHERE timestamp <= ?",  # noqa: S608 - table from _AS_OF_TABLES
            [as_of_utc_naive],
        ).fetchone()
        return count, max_ts
    finally:
        con.close()


@pytest.mark.parametrize("table", _AS_OF_TABLES)
def test_connect_asof_bounds_every_table(table: str) -> None:
    """Each of the five tables' view matches an independent raw query at the same bound."""
    connection = connect_asof(DB_PATH, lambda: AS_OF)
    try:
        view_count, view_max = connection.execute(
            f"SELECT count(*), max(timestamp) FROM main.{table}"  # noqa: S608 - table from _AS_OF_TABLES
        ).fetchone()
    finally:
        connection.close()

    as_of_utc_naive = AS_OF.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    raw_count, raw_max = _raw_bound(table, as_of_utc_naive)

    assert view_count == raw_count
    assert view_max == raw_max
    if view_max is not None:
        assert view_max <= as_of_utc_naive


def test_connect_asof_does_not_see_rows_past_the_bound() -> None:
    """The pinned file's live end still has rows after AS_OF; the view must not."""
    connection = connect_asof(DB_PATH, lambda: AS_OF)
    try:
        view_max = connection.execute("SELECT max(timestamp) FROM main.wetter").fetchone()[0]
        src_max = connection.execute("SELECT max(timestamp) FROM src.wetter").fetchone()[0]
    finally:
        connection.close()

    as_of_utc_naive = AS_OF.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    assert view_max <= as_of_utc_naive
    # `src` still reads through to the file's live end, ahead of the bound.
    assert src_max > as_of_utc_naive


_TZ_PROBE = textwrap.dedent(
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from water_assistant_agent.assistant.context import connect_asof

    as_of = datetime(2026, 1, 1, 12, 0, tzinfo=ZoneInfo("Europe/Berlin"))
    connection = connect_asof("data/water.duckdb", lambda: as_of)
    row = connection.execute("SELECT count(*), max(timestamp) FROM main.wetter").fetchone()
    print(row)
    """
)


def _run_under_tz(tz: str) -> str:
    """`connect_asof`'s bound for `_TZ_PROBE`'s fixed `as_of`, computed in a fresh
    subprocess with `TZ=`*tz* — a clean interpreter reproducing what a different
    real host would see, rather than a mid-process `os.environ['TZ']` mutation
    that timezone-aware code isn't guaranteed to pick up without `time.tzset()`.
    """
    result = subprocess.run(  # noqa: S603 - fixed interpreter, no shell, args are literals
        [sys.executable, "-c", _TZ_PROBE],
        cwd=Path(__file__).resolve().parents[2],
        env={"TZ": tz, "PATH": __import__("os").environ.get("PATH", "")},
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip(), result.stderr
    return result.stdout.strip()


def test_as_of_cut_identical_under_two_host_tz_settings() -> None:
    """The bound is a property of `as_of`'s own instant, never of the host's `TZ`
    (`decisions.md` § The as-of cut) — the one property nothing else in the pin
    set can detect a violation of.
    """
    outputs = {tz: _run_under_tz(tz) for tz in ("UTC", "America/New_York", "Asia/Tokyo")}
    assert len(set(outputs.values())) == 1, outputs


def test_production_clocked_context_reflects_date_change_without_rebuild() -> None:
    """A context whose clock advances sees the new day on its next query, no rebuild call."""
    state = {"now": datetime(2026, 4, 10, 12, 0, tzinfo=BERLIN)}
    clock = lambda: state["now"]  # noqa: E731 - a mutable-clock fixture, not a real callable
    ctx = ScenarioContext(clock, DB_PATH, lambda db, cache: None, None)

    before = ctx.db.execute_query("SELECT max(timestamp) FROM wetter").rows[0][0]
    assert before <= datetime(2026, 4, 10, 10, 0)  # UTC == CEST-2h at that date

    state["now"] = datetime(2026, 4, 20, 9, 0, tzinfo=BERLIN)
    after = ctx.db.execute_query("SELECT max(timestamp) FROM wetter").rows[0][0]

    assert after > before
    assert after <= datetime(2026, 4, 20, 7, 0)


def test_as_of_query_executor_keeps_the_bound_when_the_date_has_not_moved() -> None:
    """No rebuild — and therefore the identical connection — across same-day queries."""
    as_of = datetime(2026, 4, 10, 8, 0, tzinfo=BERLIN)
    executor = AsOfQueryExecutor(DB_PATH, lambda: as_of)
    first_connection = executor._executor._connection  # noqa: SLF001 - white-box, this test's whole point

    executor.execute_query("SELECT 1")
    executor.execute_query("SELECT 1")

    assert executor._executor._connection is first_connection  # noqa: SLF001
