"""Phase 3 (FR15 data seam) tests: warehouse state-write + tool-result merge.

T015 covers the ADK-injected ``tool_context`` state write in
``query_database_tool`` (and its inertness for direct/eval callers). T016 covers
the ``TextToSqlAgentTool`` merge of the captured result into the tool result.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from water_assistant_agent.assistant.agents.root_agent.text_to_sql_tool import (
    TextToSqlAgentTool,
    _enrich_tool_result,
)
from water_assistant_agent.assistant.tools import warehouse
from water_assistant_agent.assistant.tools.warehouse import (
    QUERY_RESULT_STATE_KEY,
    query_database_tool,
)

_SUCCESS_RESULT = {
    "status": "success",
    "columns": ["day", "mm"],
    "rows": [{"day": "2026-01-01", "mm": 3.2}],
}


class _FakeState(dict):
    """Minimal stand-in for ADK's ``State`` (item assignment + ``.get``)."""


@pytest.fixture
def _stub_execute(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the DuckDB execution path so no real database is needed."""

    async def fake_validated_execute(sql_query: str, executor: object, clock: object) -> dict:
        return dict(_SUCCESS_RESULT)

    monkeypatch.setattr(warehouse, "_validated_execute", fake_validated_execute)


@pytest.mark.asyncio
async def test_tool_is_inert_without_tool_context(_stub_execute: None) -> None:
    # Direct/eval caller passes nothing: behaviour is unchanged, no state side-effect.
    result = await query_database_tool("SELECT 1")
    assert result == _SUCCESS_RESULT


@pytest.mark.asyncio
async def test_tool_writes_result_into_state(_stub_execute: None) -> None:
    state = _FakeState()
    tool_context = SimpleNamespace(state=state)

    result = await query_database_tool("SELECT day, mm FROM rain", tool_context=tool_context)

    # The LLM-visible return is unchanged...
    assert result == _SUCCESS_RESULT
    # ...but the executed result is now stashed in session state (D4).
    captured = state[QUERY_RESULT_STATE_KEY]
    assert captured["sql_executed"] == "SELECT day, mm FROM rain"
    assert captured["columns"] == ["day", "mm"]
    assert captured["rows"] == [{"day": "2026-01-01", "mm": 3.2}]
    assert captured["result_is_likely_truncated"] is False


@pytest.mark.asyncio
async def test_error_result_writes_no_state(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_error(sql_query: str, executor: object, clock: object) -> dict:
        return {"status": "error", "error_details": "boom"}

    monkeypatch.setattr(warehouse, "_validated_execute", fake_error)
    state = _FakeState()
    tool_context = SimpleNamespace(state=state)

    result = await query_database_tool("DROP TABLE rain", tool_context=tool_context)

    assert result["status"] == "error"
    assert QUERY_RESULT_STATE_KEY not in state


# --- T016: TextToSqlAgentTool merges the captured result ----------------------


def test_root_agent_wires_the_enriched_tool() -> None:
    from water_assistant_agent.assistant.agents.root_agent.agent import root_agent

    tool = root_agent.tools[0]
    assert isinstance(tool, TextToSqlAgentTool)
    # Same tool name so the root prompt is untouched (R3).
    assert tool.name == "text_to_sql_agent"


def test_enrich_merges_columns_and_rows() -> None:
    raw = json.dumps({"status": "success", "sql": "SELECT 1", "reasoning": "why"})
    captured = {
        "columns": ["day", "mm"],
        "rows": [{"day": "2026-01-01", "mm": 3.2}],
        "result_is_likely_truncated": True,
    }
    merged = _enrich_tool_result(raw, captured)
    assert merged == {
        "status": "success",
        "sql": "SELECT 1",
        "reasoning": "why",
        "results": {
            "columns": ["day", "mm"],
            "rows": [{"day": "2026-01-01", "mm": 3.2}],
            "result_is_likely_truncated": True,
        },
    }


@pytest.mark.parametrize(
    ("raw", "captured"),
    [
        # No captured result (no query ran) → plain answer passes through unchanged.
        (json.dumps({"status": "success", "sql": "x", "reasoning": "y"}), None),
        ("just a chat answer", None),
        # Explicit non-success JSON → the error is respected, not masked (plain fallback).
        (json.dumps({"status": "error", "error_details": "boom"}), {"columns": [], "rows": []}),
    ],
)
def test_enrich_falls_back_to_plain(raw: str, captured: dict | None) -> None:
    assert _enrich_tool_result(raw, captured) == raw


def test_enrich_synthesizes_contract_from_prose_answer() -> None:
    # Real models often answer in prose; FR15 must still fire when a query ran.
    captured = {
        "sql_executed": "SELECT count(*) FROM outflow",
        "columns": ["n"],
        "rows": [{"n": 15867}],
    }
    merged = _enrich_tool_result("There are 15,867 records.", captured)
    assert merged == {
        "status": "success",
        "sql": "SELECT count(*) FROM outflow",
        "reasoning": "There are 15,867 records.",
        "results": {"columns": ["n"], "rows": [{"n": 15867}]},
    }


def test_enrich_omits_truncation_flag_when_absent() -> None:
    raw = json.dumps({"status": "success", "sql": "s", "reasoning": "r"})
    merged = _enrich_tool_result(raw, {"columns": ["a"], "rows": [{"a": 1}]})
    assert "result_is_likely_truncated" not in merged["results"]
