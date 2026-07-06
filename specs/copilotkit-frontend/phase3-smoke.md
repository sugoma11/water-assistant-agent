# Phase 3 (FR15 data seam) live smoke (T017)

Live run against `uv run water-assistant` with the real DuckDB (`data/water.duckdb`)
and the real LLM endpoint (kisski `openai/qwen3.6-35b-a3b`), throwaway SQLite session
DB. Provisioned user + one conversation, then a single text-to-SQL question.

## Enriched tool result in the live stream (FR15, SC8)

Question: _"How many outflow measurement records are there in total?"_

The `TOOL_CALL_RESULT` for `text_to_sql_agent` carried the enriched contract:

```json
{
  "status": "success",
  "sql": "SELECT COUNT(*) AS \"_col_0\" FROM \"outflow\" AS \"outflow\"",
  "reasoning": "...",
  "results": {"columns": ["_col_0"], "rows": [{"_col_0": 15867}]}
}
```

577 `TEXT_MESSAGE_CONTENT` chunks streamed before `RUN_FINISHED` — streaming intact.

## Survives history-snapshot replay (FR15, SC8, US6)

An empty-`messages` run against the same conversation returned a `MESSAGES_SNAPSHOT`
whose `tool` message replays the same enriched JSON (status / sql / results with
columns + rows). The structured FR15 payload is durable across reload because it
lives in the stored tool-result event.

## Two gaps the smoke caught (now fixed)

1. **`temp:` state is trimmed before forwarding.** `base_session_service.append_event`
   trims `temp:` deltas from the event before `AgentTool` forwards `state_delta` to
   the parent, so the wrapper saw nothing. Fixed by using a plain session-scoped key
   (`text_to_sql_query_result`).
2. **Real model answers in prose, not JSON.** The sub-agent returned a sentence, not
   the instructed `{"status","sql","reasoning"}`. Enrichment now keys off the captured
   query state and synthesizes the contract from the executed SQL when the sub-agent
   text isn't success-JSON.

## Regression: pipeline unchanged (Non-Goal, R3)

Rather than a paid MLflow eval run (kisski 429 / cost risk), the regression was
verified at the changed seam: a direct `query_database_tool('SELECT COUNT(*) …')`
call **with no `tool_context`** (the exact direct/eval caller path) returned the same
`{status: success, columns: ['n'], rows: [{'n': 15867}]}` — the injected param is
inert outside ADK. The text-to-SQL pipeline modules (builder, transpiler, validator,
executor, fixers) are untouched by Phase 3, so the pipeline behaviour is unchanged.

_R3 answer-length watch:_ the root model now sees the (≤100-row) results in its tool
result. For this COUNT query the payload is a single row; the conscious trade-off
(D4) is bounded by the existing 100-row cap and revisited in Phase 6 validation.
