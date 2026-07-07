# End-to-End Validation (T029)

Success-criteria walk (SC1–SC8) for the multi-user chat frontend, run against the
`water-assistant` backend with two provisioned users. The backend was booted with a
throwaway SQLite session DB and test JWT/admin secrets (never the shared Postgres),
exercising the exact HTTP + SSE surface the Next.js route handlers proxy to.
Frontend rendering (SC4 first-token, SC8 table) is additionally covered by the
Phase-4/5 browser smokes (T022, T026); the frontend half of `just chat` was
confirmed to build (`npm run typecheck` + `npm run lint`, both clean).

Users: `alice@example.com`, `bob@example.com` (created via the admin API).

| SC | What was checked | Result |
|----|------------------|--------|
| **SC1** | Admin CRUD key guard + sign-in: wrong/missing `X-Admin-API-Key` → 401; create alice/bob → 201; duplicate email → 409; login good → JWT + `expires_at`; login bad password → uniform 401 | ✅ PASS |
| **SC2** | Two-user isolation with *valid* creds: bob's list shows only bob's conversation; bob's `GET/PATCH/DELETE` on alice's conversation → 404; **direct agent-endpoint probe** — bob `POST /` with alice's `thread_id` → 404, unknown `thread_id` → 404, alice's own → 200 | ✅ PASS |
| **SC3** | Restart both processes → re-login works, alice's conversation list restored with its renamed title, bob still isolated (SQLite file persisted across restart) | ✅ PASS |
| **SC4** | Live streaming: `RUN_STARTED` → first `TEXT_MESSAGE_CONTENT` ~1.2 s later, deltas milliseconds apart, streamed unbuffered through the middleware stack (first parts well before completion) | ✅ PASS |
| **SC5** | Reload-then-follow-up context: after the SQL turn, a follow-up ("what was that table called?") with no restatement correctly answered `swc` from prior session history | ✅ PASS |
| **SC6** | Unauthenticated rejection of every chat/conversation route → 401; no signup path (`/auth/register` → 404 even with a valid token; middleware rejects unknown routes) | ✅ PASS |
| **SC7** | Fresh users → empty conversation list (`[]`) | ✅ PASS |
| **SC8** | Text-to-SQL enriched tool result: root agent calls `text_to_sql_agent`; `TOOL_CALL_RESULT` content = `{"status":"success","sql":"SELECT COUNT(*) … FROM \"swc\"","reasoning":…,"results":{"columns":["_col_0"],"rows":[{"_col_0":28561}]}}` — the exact shape the T026 render consumes (SQL code block + results table). Survives history-snapshot replay: the `MESSAGES_SNAPSHOT` tool message carries the same `sql`+`results` JSON | ✅ PASS |

## Notes / follow-ups

- **Upstream LLM flakiness (not a code defect).** The first SC8 attempt returned a
  transient `litellm.InternalServerError: … Error code: 500` from the sub-agent's
  model endpoint; a retry succeeded. Matches the known kisski/blablador
  drop-under-load behaviour. Consider bumping `LLM_MAX_ATTEMPTS` for interactive use.
- **Model emits chain-of-thought as answer text (R3-adjacent).** `qwen3.6-35b-a3b`
  streams its reasoning ("The user is asking…", "I will not delegate…") into the
  visible answer. This bloats answers and leaks reasoning into the chat UI. It is a
  model/prompt-format concern, orthogonal to the frontend/backend integration under
  test here — flagged for a later prompt/parse pass, not a Phase-6 blocker.
- **FR9 both mechanisms verified:** empty-run `MESSAGES_SNAPSHOT` *and* the
  `GET /conversations/{id}/messages` REST fallback (`{"messages":[…]}` with
  user/assistant-with-`toolCalls`/tool entries) both return full history including
  the enriched tool result.
