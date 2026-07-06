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


def _enrich_tool_result(raw: Any, captured: dict[str, Any] | None) -> Any:
    """Merge the captured query result into the sub-agent's JSON tool result.

    Returns *raw* unchanged when there is nothing to merge — no captured result
    (the answer used no query, or the query failed), a non-success status, or a
    result that is not the expected JSON object — so plain answers fall back to
    unmodified chat text (FR15).
    """
    if captured is None or not isinstance(raw, str):
        return raw
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw
    if not isinstance(parsed, dict) or parsed.get("status") != "success":
        return raw
    results: dict[str, Any] = {
        "columns": captured.get("columns", []),
        "rows": captured.get("rows", []),
    }
    if captured.get("result_is_likely_truncated"):
        results["result_is_likely_truncated"] = True
    parsed["results"] = results
    return parsed


class TextToSqlAgentTool(AgentTool):
    """``AgentTool`` that appends the executed query's ``results`` to the tool result."""

    @override
    async def run_async(
        self, *, args: dict[str, Any], tool_context: ToolContext
    ) -> Any:
        raw_result = await super().run_async(args=args, tool_context=tool_context)
        captured = tool_context.state.get(QUERY_RESULT_STATE_KEY)
        return _enrich_tool_result(raw_result, captured)
