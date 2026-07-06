"""``AgentTool`` subclass that surfaces the executed query result to the chat (D4, FR15).

The plain :class:`~google.adk.tools.agent_tool.AgentTool` runs ``text_to_sql_agent``
on an inner ``Runner`` and returns only that sub-agent's final text
(``{"status","sql","reasoning"}``); the executed query's ``columns``/``rows`` live
only inside the inner ``query_database_tool`` result and are otherwise swallowed.

``AgentTool`` does, however, forward the sub-agent's ``state_delta`` to the parent
tool context. ``query_database_tool`` (T015) writes its result under
:data:`~water_assistant_agent.assistant.tools.warehouse.QUERY_RESULT_STATE_KEY`, so
after the base ``run_async`` completes that captured result is readable here. This
subclass merges it into the tool result as ``results: {columns, rows}`` — the same
tool name (``text_to_sql_agent``), so the root prompt is untouched (R3).
"""

from __future__ import annotations

import json
from typing import Any

from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.tool_context import ToolContext
from typing_extensions import override

from water_assistant_agent.assistant.tools.warehouse import QUERY_RESULT_STATE_KEY


def _try_json(raw: Any) -> Any:
    if not isinstance(raw, str):
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _enrich_tool_result(raw: Any, captured: dict[str, Any] | None) -> Any:
    """Surface the executed query's results in the tool result (D4, FR15).

    Enrichment keys off the *captured query state*, not the sub-agent's output
    format: real models often answer in prose rather than the instructed
    ``{"status","sql","reasoning"}`` JSON, and FR15 must still fire whenever a
    query actually ran.

    * No captured result (the answer used no query, or the query failed) →
      *raw* is returned unchanged, so plain answers stay plain chat text.
    * Sub-agent returned success JSON → ``results`` is merged into it, preserving
      its ``sql``/``reasoning``.
    * Sub-agent returned an explicit non-success status → respected as a plain
      fallback (the error is not masked by a stale capture).
    * Sub-agent answered in prose alongside a successful query → the
      ``{"status","sql","reasoning","results"}`` contract is synthesized from the
      captured SQL and the prose kept as the reasoning.
    """
    if captured is None:
        return raw
    results: dict[str, Any] = {
        "columns": captured.get("columns", []),
        "rows": captured.get("rows", []),
    }
    if captured.get("result_is_likely_truncated"):
        results["result_is_likely_truncated"] = True

    parsed = raw if isinstance(raw, dict) else _try_json(raw)
    if isinstance(parsed, dict):
        if parsed.get("status") != "success":
            return raw
        return {**parsed, "results": results}

    return {
        "status": "success",
        "sql": captured.get("sql_executed"),
        "reasoning": raw if isinstance(raw, str) else "",
        "results": results,
    }


class TextToSqlAgentTool(AgentTool):
    """``AgentTool`` that appends the executed query's ``results`` to the tool result."""

    @override
    async def run_async(
        self, *, args: dict[str, Any], tool_context: ToolContext
    ) -> Any:
        raw_result = await super().run_async(args=args, tool_context=tool_context)
        captured = tool_context.state.get(QUERY_RESULT_STATE_KEY)
        return _enrich_tool_result(raw_result, captured)
