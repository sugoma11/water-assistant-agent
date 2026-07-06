# Phase 2 curl SSE smoke run (T014)

Live run against `uv run water-assistant` (SQLite session DB), provisioned user +
one conversation. Confirms the custom AG-UI endpoint streams unbuffered through the
full middleware stack (CorrelationId → Bearer → Logging) and that history restore
works on an empty-`messages` run (NFR2, FR9, R4).

## Empty-messages run → history-restore SSE (FR9, NFR2)

```
POST /  {threadId, messages: [], tools: [], forwardedProps: {"user_id":"SPOOFED"}}

data: {"type":"RUN_STARTED","threadId":"…","runId":"smoke-run-1"}
data: {"type":"MESSAGES_SNAPSHOT","messages":[]}
data: {"type":"RUN_FINISHED","threadId":"…","runId":"smoke-run-1"}
```

Three discrete SSE events flush separately (unbuffered). The client-supplied
`user_id: "SPOOFED"` is discarded server-side; the session is keyed by the verified
JWT `sub` (FR6).

## Ownership + auth gating

- Foreign / unknown `thread_id` on `POST /` → **404** (EC3).
- Unauthenticated `POST /` → **401** (FR5).

## Partial-append → history round-trip (C7/R2, FR9/R1)

```
POST /conversations/{id}/partial {"content":"This is a stopped partial answer."} → 201
GET  /conversations/{id}/messages
  → {"messages":[{"role":"assistant","content":"This is a stopped partial answer."}]}
POST /  {messages: []}  (empty-run snapshot)
  → MESSAGES_SNAPSHOT carries the same persisted assistant message
```

The `append_event` → `translate_to_messages` seam works against the real
`DatabaseSessionService`; the REST fallback (`GET /messages`) and the streaming
`MessagesSnapshotEvent` agree on the same history.

_Note:_ a real text-to-SQL answer (first-token-before-completion across the
CopilotKit-runtime and Next-route-handler hops) is verified in Phase 4 (T022); the
backend hop's unbuffered streaming is confirmed above.
