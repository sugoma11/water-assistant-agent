"""The tool factories, the production defaults and the pins (T035c, packet P1c).

Covers the packet's exit criterion and nothing else: ``build_toolset`` produces
independent toolsets for two ``as_of`` values running in parallel; the production
defaults are the factories' own output rather than a second construction path;
and ``just pins`` fails on a moved hash.

Two conventions carried over from T035a/T035b. Row counts are checked against an
independent raw query over the pinned ``data/water.duckdb`` rather than against a
second view, so a test cannot pass by agreeing with the implementation it is
testing. And each binding is asserted from the *far* side — the SQL a spy
executor was handed, the ``today`` a fake fetch was called with — because a test
that only inspected the object graph would pass against a tool that holds the
context and then reads a module-level singleton anyway.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import pytest
from google.adk.tools.function_tool import FunctionTool

from water_assistant_agent.assistant.agents.root_agent.agent import (
    AGENT_DESCRIPTION,
    MAX_LLM_CALLS,
    MAX_TOOL_STEPS,
    ROOT_INSTRUCTION,
    build_root_agent,
    root_agent,
    rollout_run_config,
)
from water_assistant_agent.assistant.context import ScenarioContext
from water_assistant_agent.assistant.ports import QueryResult
from water_assistant_agent.assistant.tools import gr2l as gr2l_module
from water_assistant_agent.assistant.tools import weather as weather_module
from water_assistant_agent.assistant.tools.schemas import (
    DailyWeatherRow,
    Gr2lResultRow,
    WeatherResult,
)
from water_assistant_agent.assistant.tools.site import site_now
from water_assistant_agent.assistant.tools.weather_client import (
    CompositeWeatherClient,
    make_weather_client,
)
from water_assistant_agent.assistant.tools.warehouse import SETTINGS_EXECUTOR
from water_assistant_agent.assistant.toolset import (
    GREEN_ROOF_TOOL,
    TEXT_TO_SQL_TOOL,
    TOOL_NAMES,
    WEATHER_TOOL,
    build_toolset,
    production_context,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = "data/water.duckdb"
BERLIN = ZoneInfo("Europe/Berlin")

# The same two bounds T035b uses: inside every table's record, thousands of
# `outflow` rows apart, so a leak between contexts is unmissable.
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
    """A context frozen at *as_of* over the pinned database."""
    return ScenarioContext(
        clock=lambda: as_of,
        db_path=DB_PATH,
        weather_client_factory=lambda db, cache: RecordingWeatherClient(),
        http_cache=None,
    )


class SpyExecutor:
    """A ``ReadOnlyWarehouseQuery`` that records the SQL it is handed."""

    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self.queries: list[str] = []
        self._rows = rows or []

    def execute_query(self, query: str) -> QueryResult:
        self.queries.append(query)
        return QueryResult(columns=("timestamp", "value"), rows=self._rows)


class RecordingWeatherClient:
    """A ``WeatherClient`` that records the windows it was asked for.

    The far side of the weather binding: what a wrapper actually asked its
    context for, rather than what its context happens to hold.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def fetch(self, *, start_date: str, end_date: str) -> WeatherResult:
        self.calls.append((start_date, end_date))
        return WeatherResult(
            latitude=51.0,
            longitude=12.0,
            timezone="Europe/Berlin",
            source="archive",
            data=[
                DailyWeatherRow(
                    Date=start_date,
                    tm=10.0,
                    tx=15.0,
                    tn=5.0,
                    rf=70.0,
                    precip=0.0,
                    w=5.0,
                    gs=1000.0,
                )
            ],
        )


def _declaration(tool: Any) -> Any:
    return FunctionTool(tool)._get_declaration()


# --- The exit criterion: independent toolsets for two `as_of` values ----------


def test_two_toolsets_run_in_parallel_each_on_its_own_bound() -> None:
    """Two contexts' sub-agent query tools, interleaved, each see their own cut."""
    early_tools = build_toolset(_context(EARLY_AS_OF))
    late_tools = build_toolset(_context(LATE_AS_OF))

    # `[0]` is the AgentTool wrapper; the frozen sub-agent's second tool is the
    # querier — the deepest point a context's binding has to reach.
    early_query = early_tools[0].agent.tools[1]
    late_query = late_tools[0].agent.tools[1]

    async def interleaved() -> list[dict[str, Any]]:
        calls = []
        for _ in range(4):
            calls.append(early_query(COUNT_QUERY))
            calls.append(late_query(COUNT_QUERY))
        return await asyncio.gather(*calls)

    results = asyncio.run(interleaved())
    early_expected, late_expected = _raw_count(EARLY_AS_OF), _raw_count(LATE_AS_OF)
    assert early_expected != late_expected  # the fixture itself must discriminate

    for index, result in enumerate(results):
        expected = early_expected if index % 2 == 0 else late_expected
        assert result["rows"][0]["n"] == expected


def test_two_toolsets_share_no_objects() -> None:
    """Every entry is rebuilt per context — no tool is a shared singleton."""
    early_tools = build_toolset(_context(EARLY_AS_OF))
    late_tools = build_toolset(_context(LATE_AS_OF))

    assert len(early_tools) == len(TOOL_NAMES)
    for early, late in zip(early_tools, late_tools, strict=True):
        assert early is not late
    assert early_tools[0].agent is not late_tools[0].agent
    assert early_tools[0].agent.tools[1] is not late_tools[0].agent.tools[1]


def test_the_toolset_is_declared_in_one_fixed_order() -> None:
    """Declaration order is part of the prompt, so it is the module's, not the caller's."""
    names = [
        getattr(tool, "name", None) or tool.__name__ for tool in build_toolset(_context(EARLY_AS_OF))
    ]
    assert tuple(names) == TOOL_NAMES


# --- Each tool reads its binding from the context, per call -------------------


def test_the_weather_tool_resolves_its_window_against_the_context_clock() -> None:
    """Two contexts asking for the same relative window get two different windows."""
    early_ctx, late_ctx = _context(EARLY_AS_OF), _context(LATE_AS_OF)
    early = build_toolset(early_ctx)[2]
    late = build_toolset(late_ctx)[2]
    asyncio.run(early(past_days=1))
    asyncio.run(late(past_days=1))

    # `past_days=1` is yesterday relative to *the context's* day, not the host's.
    assert early_ctx.weather.calls == [("2025-12-31", "2025-12-31")]
    assert late_ctx.weather.calls == [("2026-03-14", "2026-03-14")]


def test_the_weather_tool_reads_the_clock_per_call() -> None:
    """A moving clock moves the window without the tool being rebuilt."""
    now = EARLY_AS_OF
    client = RecordingWeatherClient()
    ctx = ScenarioContext.bound(clock=lambda: now, db=SpyExecutor(), weather=client)
    tool = weather_module.make_weather_forecast_tool(ctx)

    asyncio.run(tool(past_days=1))
    now = LATE_AS_OF
    asyncio.run(tool(past_days=1))

    assert client.calls == [("2025-12-31", "2025-12-31"), ("2026-03-14", "2026-03-14")]


def test_the_green_roof_tool_seeds_from_the_context_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The soil-moisture seed is read through ``ctx.db``, not a settings singleton."""

    async def fake_run_gr2l(rows, parameters, **kwargs):
        # `**kwargs` absorbs the wrapper's `cache=ctx.cache` (T054): this context
        # has none, and what it passes is `run_gr2l`'s business, not this test's.
        return [Gr2lResultRow(Date=rows[0].Date, ET_PM=1.0, Ssub=10.0, ET=1.0)]

    monkeypatch.setattr(gr2l_module, "run_gr2l", fake_run_gr2l)

    # `latest_measured_swc` selects `timestamp, "<column>"`, in that order.
    spy = SpyExecutor(rows=[(datetime(2026, 1, 1, 10, 0), 18.5)])
    client = RecordingWeatherClient()
    ctx = ScenarioContext.bound(clock=lambda: EARLY_AS_OF, db=spy, weather=client)
    tool = gr2l_module.make_green_roof_balance_tool(ctx)

    result = asyncio.run(tool("non_irrigated_extensive", past_days=1))

    assert result["status"] == "success"
    assert spy.queries, "the seed never reached the context's executor"
    assert result["seed"]["source"] == "measured"
    assert client.calls == [("2025-12-31", "2025-12-31")]


# --- Docstrings are candidate-addressable ------------------------------------


def test_a_candidate_docstring_reaches_the_tool_declaration() -> None:
    """What ADK sends the model is the candidate's text, not the production wording."""
    candidate = "CANDIDATE: fetch the weather, in a candidate's own words."
    tools = build_toolset(_context(EARLY_AS_OF), {WEATHER_TOOL: candidate})

    assert _declaration(tools[2]).description == candidate
    # The name and the parameter schema are untouched by a docstring override.
    assert _declaration(tools[2]).name == WEATHER_TOOL
    # An omitted component keeps the production wording rather than emptying it.
    assert _declaration(tools[1]).name == GREEN_ROOF_TOOL
    assert _declaration(tools[1]).description == _declaration(
        build_toolset(_context(EARLY_AS_OF))[1]
    ).description


def test_a_candidate_description_reaches_the_sub_agent() -> None:
    """The sub-agent's optimizable text is its outward description (§2)."""
    candidate = "CANDIDATE: the analyst, described differently."
    tools = build_toolset(_context(EARLY_AS_OF), {TEXT_TO_SQL_TOOL: candidate})

    assert tools[0].agent.description == candidate
    # ...and only that: the frozen instruction is untouched.
    assert tools[0].agent.static_instruction == build_toolset(_context(EARLY_AS_OF))[
        0
    ].agent.static_instruction


def test_an_unknown_docstring_key_raises() -> None:
    """A silently dropped component would be scored as though it had been applied.

    The key is a plausible *misspelling* of a real tool rather than an invented
    name, which is the realistic failure now that every §3 tool but the plotter
    exists: a registry entry keyed ``reference_lookup`` looks applied and is not.
    """
    with pytest.raises(ValueError, match="reference_lookup"):
        build_toolset(_context(EARLY_AS_OF), {"reference_lookup": "not a tool"})


def test_build_root_agent_refuses_docstrings_and_tools_together() -> None:
    """The same failure from the other side: the docstrings would be ignored."""
    ctx = _context(EARLY_AS_OF)
    with pytest.raises(ValueError, match="not both"):
        build_root_agent(ctx, docstrings={WEATHER_TOOL: "x"}, tools=build_toolset(ctx))


# --- The production defaults are the factories' own output --------------------


def test_the_production_context_is_the_lazy_settings_binding() -> None:
    """Production pairs the site clock with the settings executor, and stays lazy."""
    ctx = production_context()

    assert ctx.clock is site_now
    assert ctx.db is SETTINGS_EXECUTOR
    assert production_context() is ctx  # one context, not one per import site


def test_both_construction_paths_reach_the_composite_weather_client() -> None:
    """``__init__`` and ``bound`` alike hand a tool the same two-source client (T043).

    Neither may be a special case: production answers the same window from the
    same source a case would, and a context whose ``weather`` were still ``None``
    would fail only once a tool reached for it.
    """
    harness_ctx = ScenarioContext(
        clock=lambda: EARLY_AS_OF,
        db_path=DB_PATH,
        weather_client_factory=make_weather_client,
        http_cache=None,
    )

    for ctx in (harness_ctx, production_context()):
        assert isinstance(ctx.weather, CompositeWeatherClient)
        # The station half reads *this* context's executor, not a singleton.
        assert ctx.weather._station._executor is ctx.db


def test_the_module_level_tools_are_gone() -> None:
    """There is no second construction path left to drift from the factories.

    The teeth of this packet's "production defaults are the factories' output":
    a module-level ``get_weather_forecast_tool`` could be imported and bound to
    nothing, which is exactly the singleton the seam removes.
    """
    assert not hasattr(weather_module, "get_weather_forecast_tool")
    assert not hasattr(gr2l_module, "predict_green_roof_water_balance_tool")


def test_the_production_root_agent_matches_a_fresh_build() -> None:
    """``root_agent`` is ``build_root_agent(production_context())`` and nothing else."""
    rebuilt = build_root_agent(production_context())

    assert root_agent.name == rebuilt.name
    assert root_agent.description == rebuilt.description == AGENT_DESCRIPTION
    assert root_agent.static_instruction == rebuilt.static_instruction == ROOT_INSTRUCTION
    assert [_name_of(tool) for tool in root_agent.tools] == list(TOOL_NAMES)

    for live, fresh in zip(root_agent.tools, rebuilt.tools, strict=True):
        assert _name_of(live) == _name_of(fresh)
        assert _doc_of(live) == _doc_of(fresh)
        assert live is not fresh  # a rebuild, not a shared singleton


def test_the_production_instruction_provider_reads_the_site_clock() -> None:
    """Production's date block is rendered from ``site_now``, per invocation."""
    rendered = root_agent.instruction(None)
    assert site_now().strftime("%Y-%m-%d") in rendered


def test_the_rollout_cap_is_the_tool_steps_plus_the_answer() -> None:
    """§2's ~6 tool steps, expressed in the unit ADK actually counts."""
    assert MAX_LLM_CALLS == MAX_TOOL_STEPS + 1
    assert rollout_run_config().max_llm_calls == MAX_LLM_CALLS


def _name_of(tool: Any) -> str:
    return getattr(tool, "name", None) or tool.__name__


def _doc_of(tool: Any) -> str | None:
    agent = getattr(tool, "agent", None)
    return agent.description if agent is not None else tool.__doc__


# --- `just pins` --------------------------------------------------------------


def _load_check_pins():
    """Import ``scripts/check_pins.py`` by path; ``scripts/`` is not a package."""
    spec = importlib.util.spec_from_file_location(
        "check_pins", REPO_ROOT / "scripts" / "check_pins.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_committed_pins_match_the_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pins file is honest about the tree it is committed with."""
    check_pins = _load_check_pins()
    monkeypatch.setattr(sys, "argv", ["check_pins.py"])
    assert check_pins.main() == 0


def test_the_pins_check_fails_on_a_moved_hash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A moved database hash fails the check and is named in the report."""
    check_pins = _load_check_pins()
    moved = tmp_path / "pins.json"
    shutil.copy(check_pins.PINS_PATH, moved)
    document = json.loads(moved.read_text())
    document["pins"]["water_duckdb_sha256"] = "0" * 64
    moved.write_text(json.dumps(document, indent=2, sort_keys=True))

    monkeypatch.setattr(check_pins, "PINS_PATH", moved)
    monkeypatch.setattr(sys, "argv", ["check_pins.py"])
    assert check_pins.main() == 1
    report = capsys.readouterr().out
    assert "MOVED" in report
    assert "water_duckdb_sha256" in report


def test_an_unpinned_slot_is_reported_but_does_not_fail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A null is a slot a later packet fills, not a failure — P4/P8 depend on it."""
    check_pins = _load_check_pins()
    unpinned = tmp_path / "pins.json"
    shutil.copy(check_pins.PINS_PATH, unpinned)
    document = json.loads(unpinned.read_text())
    document["pins"]["water_duckdb_sha256"] = None
    unpinned.write_text(json.dumps(document, indent=2, sort_keys=True))

    monkeypatch.setattr(check_pins, "PINS_PATH", unpinned)
    monkeypatch.setattr(sys, "argv", ["check_pins.py"])
    assert check_pins.main() == 0
    assert "OPEN     water_duckdb_sha256" in capsys.readouterr().out


def test_just_pins_is_wired_to_the_check() -> None:
    """The recipe exists and runs the checker — the exit criterion is ``just pins``."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    completed = subprocess.run(
        ["just", "pins"], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "water_duckdb_sha256" in completed.stdout
