"""``FunctionTool`` subclass that hands the drawn series to the chat (T097, §3.6).

``plot_timeseries`` returns the resolved spec, per-series statistics and an
``artifact_ref`` — and no values at all. The renderer needs the values, so the
tool stashes them under
:data:`~water_assistant_agent.assistant.tools.plot.PLOT_SERIES_STATE_KEY` and this
wrapper joins the two halves into the payload the chat reads
(:data:`PLOT_PAYLOAD_STATE_KEY`), leaving the returned dict untouched.

**It is the ``QUERY_RESULT_STATE_KEY`` pattern with the merge pointed somewhere
else, and the difference is the whole design.** ``TextToSqlAgentTool`` merges its
capture into the value it *returns*, because §3.1's capped rows are allowed to
reach the model; §3.6 forbids a plot's series there — the series' consumer is the
renderer, not the LLM, which is what lets plotting satisfy principle 3 outright
instead of being capped by it. So the merged payload is written to session state,
which reaches the frontend without passing through the model, and the model's
copy of the tool result stays spec, statistics and the handle naming the payload.

Two consumers, one seam, and a third that touches neither: a harness rollout
calls the tool function directly with no ``ToolContext``, so an evaluation writes
no state key, reads no state key, and scores the returned spec alone. Rendering
stays the unscored side effect §3.6 says it is.
"""

from __future__ import annotations

from typing import Any

import structlog
from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.tool_context import ToolContext
from typing_extensions import override

from water_assistant_agent.assistant.tools.plot import PLOT_SERIES_STATE_KEY

logger = structlog.get_logger(__name__)

PLOT_PAYLOAD_STATE_KEY = "plot_timeseries_payload"
"""Session-state key holding the merged chart — the spec with its values.

One slot, overwritten per plot, like the query result's. What the chat renders is
this payload; what the model reads is the tool result beside it, and the two are
joined by ``artifact_ref``.
"""


def merge_plot_payload(
    state: Any, result: Any, stashed: Any
) -> dict[str, Any] | None:
    """Join a plot *result* with the *stashed* points, and write it to *state*.

    The join is on ``artifact_ref`` rather than on "the last thing stashed", so a
    stale stash — a previous plot in the same session, a result that came back an
    error — cannot be drawn under this call's spec. Returns the payload that was
    written, or ``None`` when there is nothing to render.

    Series are paired by position and the pairing is checked, not assumed: the
    two halves are built from one list in one call, so a mismatch means the
    stash is not this result's and the safe thing is to render nothing rather
    than to label one roof's values with another's.
    """
    if not isinstance(result, dict) or result.get("status") != "success":
        return None
    if not isinstance(stashed, dict) or stashed.get("artifact_ref") != result.get(
        "artifact_ref"
    ):
        return None

    specs = result.get("series") or []
    points = stashed.get("series") or []
    if len(specs) != len(points):
        logger.warning(
            "A stashed plot series does not match its result",
            specs=len(specs),
            stashed=len(points),
        )
        return None

    merged = []
    for spec, drawn in zip(specs, points, strict=True):
        if (spec.get("source"), spec.get("variable"), spec.get("roof")) != (
            drawn.get("source"),
            drawn.get("variable"),
            drawn.get("roof"),
        ):
            logger.warning("A stashed plot series does not match its spec")
            return None
        merged.append({**spec, "points": drawn.get("points", [])})

    payload = {**result, "series": merged}
    state[PLOT_PAYLOAD_STATE_KEY] = payload
    return payload


class PlotTimeseriesTool(FunctionTool):
    """``FunctionTool`` that merges the stashed series into the chat's payload."""

    @override
    async def run_async(
        self, *, args: dict[str, Any], tool_context: ToolContext
    ) -> Any:
        result = await super().run_async(args=args, tool_context=tool_context)
        merge_plot_payload(
            tool_context.state, result, tool_context.state.get(PLOT_SERIES_STATE_KEY)
        )
        # Returned unchanged: this is the model's copy, and it carries no values.
        return result
