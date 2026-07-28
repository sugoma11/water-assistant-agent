# Implementation Plan: Multi-User Chat Frontend for the Water Assistant

Spec: [`spec.md`](./spec.md)

## Technical Context

The backend today is `src/water_assistant_agent/assistant/`: `bootstrap.py` wires a
FastAPI app with `/health`, `CorrelationIdMiddleware`, `LoggingMiddleware`, an
*optional* `BearerTokenMiddleware` (HS256 JWT via `AGENT_JWT_SECRET`, skipped with a
warning when unset), and the AG-UI ADK endpoint mounted at `/` through
`ag_ui_adk.add_adk_fastapi_endpoint`. The `ADKAgent` is constructed with the fixed
identity `user_id=settings.user_id` (`"demo_user"`) and an optional
`DatabaseSessionService` (`session_db_url`; falls back to in-memory). Settings follow
the `WATER_ASSISTANT_*` pydantic-settings convention (`settings.py`), logging is
structlog with correlation-id contextvars (NFR1/NFR5 groundwork already exists).

**Seams confirmed from installed source** (not assumptions):

- **Per-request identity (FR6).** `ADKAgent` accepts `user_id_extractor:
  Callable[[RunAgentInput], str]`, mutually exclusive with the static `user_id`
  (`ag_ui_adk/adk_agent.py:55-115`, `_get_user_id` at `:210`). The extractor sees only
  the `RunAgentInput`, not the HTTP request — so the verified identity must be placed
  *into* the input server-side. `add_adk_fastapi_endpoint` is a ~50-line wrapper
  (`ag_ui_adk/endpoint.py:15-80`): we replace it with our own endpoint that (a) reads
  the JWT claims the existing `BearerTokenMiddleware` stored in
  `request.state.token_claims`, (b) overwrites `input_data`'s forwarded props with the
  verified user id (client-supplied values are discarded — this is what makes the
  identity *verified*), and (c) streams exactly as the upstream endpoint does.
- **History restore is built in (FR9, FR12).** `ADKAgent.run` treats an AG-UI run with
  empty `messages` and no tools as a history request: it emits `RUN_STARTED`, a
  `MessagesSnapshotEvent` translated from the stored ADK session events, and
  `RUN_FINISHED` (`adk_agent.py:380-421`). The snapshot includes assistant
  `tool_calls` and `ToolMessage` results (`event_translator.py:336-353`), so the FR15
  structured render survives reloads. Messages therefore live **only** in the ADK
  session store; the frontend never persists messages itself.
- **Session keying (FR11).** AG-UI `thread_id` is the ADK `session_id`; sessions are
  keyed `(app_name, user_id, session_id)`. One conversation ↔ one agent session falls
  out of using the conversation id as the AG-UI thread id.
- **FR15 data gap.** `AgentTool` runs the text-to-SQL sub-agent on an internal
  `Runner` and returns only the sub-agent's final text as the tool result — inner
  events are swallowed — but it **forwards `state_delta` to the parent session**
  (`google/adk/tools/agent_tool.py:277-300`). The sub-agent's final JSON is
  `{"status","sql","reasoning"}` (`agents/text_to_sql/agent.py:36-37`); the executed
  SQL's `columns`/`rows` exist only inside the inner `query_database_tool` result
  (`tools/warehouse.py:113-117`, already capped at 100 rows). So the query results
  must be exported through session state (D4 below).
- **Existing JWT middleware fits.** `BearerTokenMiddleware` already rejects
  missing/invalid/expired HS256 tokens with 401 and a `_PUBLIC_ROUTES` frozenset
  (`middlewares.py:85-147`); it needs `/auth/login` (and the admin router, which has
  its own key check) added to the public set, and it must become mandatory (FR6)
  instead of optional.
- **Dev-loop reality.** `pytest`, `pytest-asyncio`, and `httpx` are already dev
  dependencies (`pyproject.toml`), so API-level isolation tests (NFR3) need no new
  tooling. `sqlalchemy` is already in the environment transitively via
  `google-adk`'s `DatabaseSessionService`; it becomes an explicit dependency.

Frontend constraints (C3): CopilotKit as framework, AG-UI to the existing endpoint.
The standard shape is a Next.js app hosting the CopilotKit **runtime** in a route
handler; the runtime connects to the backend with `@ag-ui/client`'s `HttpAgent`. The
browser never talks to FastAPI directly — every call goes through Next.js route
handlers on one origin (no CORS, and the auth token can live in an httpOnly cookie,
NFR1).

## Key architectural decisions

**D1 — the FastAPI backend owns all security state; the frontend stays thin.** User
accounts, password verification, token minting, conversation metadata, and ownership
enforcement all live in new FastAPI routers next to the existing agent endpoint —
where the ADK session store and the existing JWT/logging/settings conventions already
are. The Next.js app is a client: login form, sidebar, chat, and three kinds of route
handlers (auth cookie management, a thin authenticated proxy to the backend REST API,
and the CopilotKit runtime). Rejected alternative: a frontend-owned user store
(Prisma/NextAuth) would split security enforcement across two codebases and duplicate
the SQL layer the backend already has.

**D2 — one token, verified where it matters.** `POST /auth/login` (FastAPI) verifies
email+password and mints an HS256 JWT signed with the existing `AGENT_JWT_SECRET`
(`sub` = user id, `exp` = now + configurable lifetime, default 7 days, C8). The
Next.js login route handler stores it in an httpOnly, `SameSite=Lax` cookie. Every
route handler that talks to the backend (REST proxy and CopilotKit runtime) copies
the cookie value into `Authorization: Bearer …`. The backend's existing
`BearerTokenMiddleware` verifies it on every non-public route — including the agent
endpoint, satisfying FR6 with machinery that already exists. Logout clears the
cookie; JWTs stay stateless. Prompt revocation (EC5) comes from a per-request DB
existence check on the `sub` user, not from token state.

**D3 — conversations table for metadata, ADK sessions for messages.** A
`conversations` table (id = AG-UI thread id, owner, title, timestamps) is the single
authority for *whose* conversation exists (FR7, FR8, FR13); the ADK
`DatabaseSessionService` in the **same database** (A1) remains the single authority
for *what was said* (FR11, FR12). Nothing stores messages twice. The agent endpoint
only accepts a `thread_id` that exists in `conversations` and belongs to the verified
user — a foreign or unknown id gets the same 404 as a nonexistent one (EC3), which
also prevents the subtle hole where posting another user's thread id would silently
create a fresh, empty ADK session under the requester's identity.

**D4 — FR15 rides the tool-result channel, fed by state forwarding.**
`query_database_tool` gains an ADK-injected `tool_context: ToolContext` parameter
(excluded from the LLM-visible schema — prompts untouched) and writes its success
result (`sql_executed`, `columns`, `rows`) into session state. `AgentTool` already
forwards that state delta to the parent. A small `AgentTool` subclass (same tool name
`text_to_sql_agent`, so the root prompt is untouched) merges the captured result into
the returned tool result: `{"status","sql","reasoning","results":{"columns","rows"}}`.
The AG-UI stream then carries SQL + rows inside the root agent's
`ToolCallResult`/`ToolMessage`, which (per the history-snapshot finding above) also
restores after reload. The frontend registers a CopilotKit render for the
`text_to_sql_agent` tool call: SQL as a highlighted code block, results as a table,
inside the assistant turn (C9, SC8). Conscious deviation, recorded: the root model now
sees the (≤100-row) results in its tool result where today it sees only
`sql`/`reasoning` — this is the minimal channel that gets data to the chat at all, and
arguably what the summarizing root agent should have seen anyway; flagged in R3.

**D5 — auth becomes mandatory; the demo identity is deleted.** `jwt_secret_key` and
`session_db_url` move from optional to required for the assistant service:
`create_bootstrap` refuses to start without them (clear startup error naming the env
var) instead of warning-and-falling-open. `settings.user_id` (`"demo_user"`) is
removed. This deliberately changes the bare `water-assistant` startup contract — FR6
and FR12 demand it — while every other existing command (`text2sql-*`, experiments,
docker-compose) is untouched (FR14). Dev default database: a SQLite URL documented in
`.example.env` (one file serves ADK sessions + users + conversations; Postgres for
anything shared).

**D6 — admin API is key-only and fails closed.** `/admin/users/*` is authorized
exclusively by a constant-time comparison against `WATER_ASSISTANT_ADMIN_API_KEY`
from settings (FR2). When the setting is absent every admin request gets 503 with a
clear "admin API not configured" body (EC6). The admin router is exempt from JWT
middleware but never from its own key check; the key is read from a header
(`X-Admin-API-Key`) and never logged (NFR1 — the existing `LoggingMiddleware` logs
URLs, not headers, so no change needed there, but the admin router must not echo the
key in errors either).

## Architecture / Components

```
repo root
├── src/water_assistant_agent/assistant/        [existing package, extended]
│   ├── bootstrap.py            wires new routers + custom AG-UI endpoint; requires
│   │                           jwt secret + session db; drops demo user_id
│   ├── settings.py             + admin_api_key, auth_token_ttl (7d default, C8);
│   │                           user_id removed; session_db_url/jwt required
│   ├── middlewares.py          BearerTokenMiddleware: public set += /auth/login,
│   │                           /admin prefix (admin has its own key guard)
│   ├── db.py            [new]  SQLAlchemy engine/session over session_db_url;
│   │                           metadata.create_all at startup (app_users,
│   │                           conversations; ADK creates its own tables)
│   ├── auth/            [new]  password hashing (argon2), JWT mint/verify helpers,
│   │                           current_user dependency (decodes claims set by the
│   │                           middleware + EC5 user-existence check)
│   ├── routers/         [new]
│   │   ├── admin_users.py      FR1-FR3: CRUD, X-Admin-API-Key, EC4/EC6
│   │   ├── auth.py             POST /auth/login, GET /auth/me
│   │   ├── conversations.py    FR7/FR8/FR13: list/create/rename/delete,
│   │   │                       GET /{id}/messages (history fallback),
│   │   │                       POST /{id}/partial (C7 durability, R2)
│   │   └── agent.py            custom AG-UI endpoint replacing
│   │                           add_adk_fastapi_endpoint: ownership check (EC3),
│   │                           verified user id into forwarded props,
│   │                           last-activity touch, unbuffered SSE (NFR2)
│   └── agents/root_agent/      AgentTool subclass merging state-captured query
│       + tools/warehouse.py    results into the tool result (D4, FR15)
└── web/                                        [new Next.js app, own toolchain]
    ├── middleware.ts           unauthenticated -> /login (only reachable page, FR5)
    ├── app/login/              email+password form (no signup path, FR3)
    ├── app/(chat)/             sidebar (list/new/rename/delete/switch) + chat pane
    ├── app/api/auth/[...]      login/logout route handlers; httpOnly cookie (D2)
    ├── app/api/backend/[...]   authenticated streaming proxy to FastAPI REST
    ├── app/api/copilotkit/     CopilotKit runtime; HttpAgent -> FastAPI "/" with
    │                           Bearer from cookie + X-Request-ID passthrough (NFR5)
    └── components/             CopilotChat wrapper (threadId = conversation id),
                                text2sql render (SQL code block + results table),
                                markdown rendering, stop button (FR10/C7)
```

Request flows:

- **Chat run**: browser → `/api/copilotkit` (cookie) → CopilotKit runtime →
  `HttpAgent` with Bearer JWT → FastAPI `/` → JWT middleware verifies → custom
  endpoint checks `conversations` ownership of `thread_id`, injects verified user id
  → `ADKAgent.run` (user_id_extractor reads the injected id) → SSE streamed back
  through runtime and route handler unbuffered (NFR2).
- **History load** (conversation switch / reload): same path with empty `messages` →
  `MessagesSnapshotEvent` from the ADK session. Fallback if the CopilotKit client
  can't drive this cleanly (R1): `GET /conversations/{id}/messages`, implemented by
  reusing `ag_ui_adk`'s `EventTranslator.translate_to_messages` over the stored
  session, fed into the chat via CopilotKit's message-setting API.
- **Sidebar/auth**: browser → `/api/backend/*` proxy → FastAPI routers.

## Data Model

- **User account** (`app_users`; name avoids clashing with ADK's `user_states`) — id
  (UUID pk), email (unique, case-normalized), password_hash (argon2), display_name
  (nullable), created_at, updated_at. Only the admin API writes it (FR1, FR3).
- **Conversation** (`conversations`) — id (UUID pk; *is* the AG-UI thread id and ADK
  session id), user_id (fk → app_users, cascade delete), title, created_at,
  last_activity_at. Title derived server-side at creation from the optional first
  message (truncation, C6); `PATCH` renames (FR7). List ordered by last_activity_at
  desc (FR8). Deleting a conversation deletes the row **and** the ADK session;
  deleting a user cascades rows and deletes each ADK session (EC5, C5).
- **Message** — not a project table: ADK `events` under session
  `(app_name, verified user id, conversation id)` are the message store (D3, FR12).
- **Auth session/token** — stateless JWT: `sub` (user id), `email`, `exp`
  (`auth_token_ttl`, default 7 days). No server-side token table; revocation on user
  deletion via the per-request existence check (EC5).
- **Admin API key** — `WATER_ASSISTANT_ADMIN_API_KEY` setting; compared
  constant-time; absent ⇒ admin API fails closed (FR2, EC6).
- **Text-to-SQL result (FR15)** — transient, inside the enriched tool result:
  `{"status","sql","reasoning","results":{"columns","rows"}}`; durable only as part
  of the stored tool-result event, which is exactly what the history snapshot
  replays.

## Interfaces / Contracts

### Backend REST (all JSON; errors follow existing exception-handler conventions)

| Route | Auth | Behavior |
|---|---|---|
| `POST /admin/users` | admin key | create; 409 on duplicate email (EC4) |
| `GET /admin/users` | admin key | list (no password hashes in responses) |
| `PATCH /admin/users/{id}` | admin key | update email/display_name/password |
| `DELETE /admin/users/{id}` | admin key | delete user + conversations + ADK sessions (EC5) |
| `POST /auth/login` | public | email+password → `{access_token, expires_at}`; 401 on bad credentials (uniform message) |
| `GET /auth/me` | JWT | current user (frontend session probe) |
| `GET /conversations` | JWT | own conversations, last-activity desc (FR8) |
| `POST /conversations` | JWT | `{first_message?}` → `{id, title}`; title truncated from first message or default (C6, EC8) |
| `PATCH /conversations/{id}` | JWT | rename; 404 if not owned/unknown (EC3) |
| `DELETE /conversations/{id}` | JWT | delete row + ADK session; 404 pattern per EC3 |
| `GET /conversations/{id}/messages` | JWT | AG-UI-shaped message history (FR9 fallback, R1) |
| `POST /conversations/{id}/partial` | JWT | append a stopped answer's received text as an assistant event via `session_service.append_event` (C7, R2) |
| `POST /` (AG-UI) | JWT | ownership check on `thread_id` (404 per EC3) → inject verified user id → stream |

All admin-key comparisons use `secrets.compare_digest`; all `conversations` queries
filter by the verified user id in SQL (server-side isolation, FR13/NFR3), never by
client-supplied identity.

### Identity injection contract (FR6)

The custom agent endpoint **replaces** whatever the client sent in the
forwarded-props identity slot with the id from `request.state.token_claims["sub"]`
before calling `ADKAgent.run`; `ADKAgent` is constructed with a `user_id_extractor`
that reads exactly that slot and raises if absent. No code path lets a
client-supplied identity reach session keying. The static `user_id` parameter and
`settings.user_id` are deleted.

### Frontend contracts

- Cookie: httpOnly, `SameSite=Lax`, `Secure` in production; value is the backend JWT;
  maxAge mirrors `expires_at`. Route handlers are the only readers.
- 401 from any proxy call (expired/invalid token, deleted user) clears the cookie and
  redirects to `/login` without touching conversation state (EC1, EC5).
- CopilotKit runtime route builds the `HttpAgent` per request with the current
  cookie's Bearer header and forwards/creates `X-Request-ID` so backend logs correlate
  (NFR5). The route handler streams — no buffering of the SSE body (NFR2).
- Stop button aborts the run (CopilotKit stop API); the received partial text is kept
  in the UI and persisted via `POST /conversations/{id}/partial` (C7).
- The `text_to_sql_agent` tool render: `status=success` → SQL code block +
  results table (columns/rows, note when `result_is_likely_truncated`); non-success
  or missing `results` → plain markdown fallback (FR15's "answers without text-to-SQL
  output render as plain chat").

### Configuration (env, following the existing convention)

| Variable | New/changed | Meaning |
|---|---|---|
| `AGENT_JWT_SECRET` | now required | JWT signing secret (existing alias kept) |
| `WATER_ASSISTANT_SESSION_DB_URL` | now required | one SQL DB: ADK sessions + app_users + conversations (A1); dev default documented as SQLite file URL |
| `WATER_ASSISTANT_ADMIN_API_KEY` | new | admin API key (FR2); absent ⇒ admin API fails closed (EC6) |
| `WATER_ASSISTANT_AUTH_TOKEN_TTL_DAYS` | new, default 7 | session lifetime (C8) |
| `WATER_ASSISTANT_USER_ID` | removed | demo identity gone (FR6) |
| web: `BACKEND_URL` | new (`web/.env`) | FastAPI base URL for route handlers |

New backend dependencies: `argon2-cffi` (hashing), `sqlalchemy` (made explicit).
Frontend toolchain: Next.js (App Router, TypeScript) + `@copilotkit/react-core`,
`@copilotkit/react-ui`, `@copilotkit/runtime`, `@ag-ui/client` — versions pinned at
implementation time against current docs (OQ1).

### Developer experience (NFR4)

`just assistant` (backend, exists as `uv run water-assistant`), `just web`
(`npm run dev` in `web/`), `just chat` (both together for the e2e setup). README
gains a frontend section + admin-API quickstart (create the first user with curl).
Nothing under `src/` changes its CLI surface except the now-required env vars for
`water-assistant` itself (D5).

## Phases / Dependencies

1. **Auth & user foundation (backend).** `db.py`, `app_users`, argon2 hashing,
   settings changes (D5, D6), admin router (FR1–FR3, EC4, EC6), auth router (FR4),
   middleware public-routes update, mandatory-config startup errors. API tests:
   admin CRUD + key auth + fail-closed, login/expiry (EC1 server side), duplicate
   email. *Blocks everything else.*
2. **Conversations & verified agent identity (backend).** `conversations` table +
   router (FR7/FR8/FR13, EC3, EC8), custom AG-UI endpoint with ownership check +
   identity injection + last-activity touch (FR6), history endpoint + partial-append
   endpoint, demo identity removal. Tests: two-user isolation at the API level
   (SC2/NFR3), foreign-id 404 parity (EC3), unauthenticated rejection (FR5/SC6),
   agent-endpoint identity injection (unit-level with a stub agent). Verify SSE still
   streams through the middleware stack (NFR2) with a curl smoke run.
3. **FR15 data seam (backend).** `tool_context` state write in
   `query_database_tool`, `AgentTool` subclass merging results (D4), regression check
   that the text-to-SQL pipeline itself is untouched (Non-Goal): existing behavior
   exercised via one live agent run inspecting the AG-UI event stream for the
   enriched tool result.
4. **Frontend scaffold.** `web/` Next.js app, login page + cookie route handlers +
   route guard middleware (FR3, FR4, FR5), backend proxy, CopilotKit runtime route
   wired to the agent endpoint with Bearer + correlation id. Milestone: signed-in
   user chats in a single conversation with streaming (SC4 observable).
5. **Chat UX.** Sidebar (list/new/rename/delete/switch; last-activity order),
   threadId = conversation id, history restore on switch/reload (empty-run snapshot,
   fallback per R1), stop + partial persistence (C7), text2sql render (C9/SC8),
   markdown rendering, EC2 error surface, EC7 (refetch list on focus), EC8 empty
   conversations.
6. **End-to-end validation & docs.** Walk every SC1–SC8 against `just chat` with two
   provisioned users, including the restart test (SC3) and the API-level isolation
   probes (NFR3); `.example.env` + README + justfile updates; retire stale
   `.example.env` demo-identity lines.

Phases 1–3 are backend-only and independently testable with pytest/curl; 4–5 depend
on 1–2 (and 5 on 3 for the render). This keeps each phase deliverable and testable
on its own (incremental delivery).

## Risks & Open Questions

- **R1: CopilotKit thread switching & the empty-run history snapshot.** The backend
  contract is verified from `ag_ui_adk` source, but whether the current CopilotKit
  client cleanly (re)connects an AG-UI agent per `threadId` and applies a
  `MessagesSnapshotEvent` on an empty run must be verified against the pinned
  CopilotKit version early in Phase 5. Fallback (fully project-owned):
  `GET /conversations/{id}/messages` + CopilotKit's message-setting API. Either way
  FR9 is met; only the mechanism differs.
- **R2: durability of a stopped answer (C7 × FR12).** When the client aborts the SSE
  request, the ADK runner's task is cancelled and the in-flight assistant turn may
  never be committed to the session — the partial would survive in the UI but not a
  reload. Primary mitigation is the `POST /conversations/{id}/partial` append
  endpoint (server-side `append_event` into the same session). Phase-5 verification:
  stop mid-answer, reload, history shows the partial. If ADK turns out to commit
  partials itself, the endpoint becomes a no-op to drop.
- **R3: root model now sees query rows (D4).** Bounded by the existing 100-row cap;
  changes the root agent's context, not its prompts or the pipeline. Recorded as the
  conscious FR15 trade-off; watch answer length in Phase-6 validation.
- **R4: streaming through two proxies (NFR2).** Starlette's `BaseHTTPMiddleware`
  passes streaming bodies through today (the AG-UI dojo works against this stack),
  but the two *new* hops — CopilotKit runtime and the Next.js route handlers — must
  be smoke-tested for buffering (first tokens visible before completion, SC4) in
  Phase 4.
- **R5: `ag_ui_adk` `SessionManager` caches session metadata process-globally.**
  Conversation deletion goes through the session service directly; a stale cache
  entry can't resurrect access because the `conversations` ownership check runs
  before any session lookup (D3), but Phase-2 tests should delete-then-repost to
  prove it.
- **R6: SQLite as the dev database** serves three writers (ADK sessions, users,
  conversations) in one uvicorn process — fine single-node (A4), and Postgres is a
  URL swap; the compose file already runs Postgres images if a shared dev DB is ever
  wanted. Not a production concern per Non-Goals.
- **OQ1: exact frontend package versions** (Next.js / CopilotKit / `@ag-ui/client`
  compatibility matrix) — resolved at Phase-4 start against current docs, pinned in
  `web/package.json`.
- **OQ2 (decision taken): title derivation is truncation**, not an LLM summary — C6
  allows either; truncation keeps the agent untouched and adds no cost. Revisitable
  behind the same `POST /conversations` contract.
- **OQ3 (decision taken): no Alembic yet.** `metadata.create_all` on startup for the
  two new tables (ADK manages its own). First schema change introduces migrations;
  noted in README.

## Testing Strategy

- **Backend API tests (pytest + httpx, extend as each router lands):** admin CRUD
  incl. duplicate email (EC4), wrong/missing/unconfigured key (FR2, EC6, SC1); login
  success/failure + expired-token 401 (FR4, EC1); the two-user isolation matrix —
  sidebar list, foreign `GET/PATCH/DELETE`, foreign `thread_id` on the agent
  endpoint, all with *valid* credentials (FR13, NFR3, SC2, EC3); unauthenticated
  rejection of every chat/conversation route (FR5, SC6); deleted-user revocation
  (EC5); identity injection overrides client-supplied props (FR6).
- **Live smoke runs per phase:** curl SSE run against the agent endpoint (Phase 2,
  NFR2); one real text-to-SQL question inspecting the enriched tool result in the
  event stream (Phase 3, FR15); browser first-token-before-completion (Phase 4, SC4).
- **End-to-end SC walk (Phase 6):** SC1–SC8 executed against `just chat` with two
  provisioned users, including full restart of both apps (SC3), reload-then-follow-up
  (SC5), and the stop/partial reload check (C7/R2). Frontend logic stays thin enough
  that the SC walk + API tests carry verification; no separate frontend unit-test
  suite unless Phase 5 grows nontrivial client state.
- **Regression (FR14/Non-Goals):** `text2sql-*` CLIs and experiment flows never touch
  the changed modules except `tools/warehouse.py` — one standalone eval smoke run
  confirms the `tool_context` parameter is inert outside the agent (ADK injects it;
  direct callers pass nothing).

## Traceability

| Requirement | Where addressed |
|---|---|
| FR1–FR3, EC4, EC6, SC1 | admin router + key guard, fail-closed (D6); no signup path anywhere (frontend has only /login) |
| FR4, C8, EC1 | auth router + JWT TTL setting (D2); cookie clear + redirect on 401 |
| FR5, SC6 | JWT middleware mandatory on all non-public routes; Next route guard |
| FR6 | identity-injection contract; demo `user_id` deleted (D5) |
| FR7, FR8, C6, EC8 | conversations router; server-derived truncated titles; last-activity ordering |
| FR9, R1 | empty-run MessagesSnapshot (verified seam) + owned REST fallback |
| FR10, C7, R2 | streaming end-to-end (NFR2 checks); stop + partial-append endpoint |
| FR11 | conversation id = thread id = ADK session id under verified user (D3) |
| FR12, SC3 | single SQL DB for sessions + metadata, now required (D5) |
| FR13, EC3, SC2, NFR3 | SQL-level owner filtering; uniform 404; ownership gate on the agent endpoint; two-user API tests |
| FR14, C4 | `web/` with own toolchain; backend package extended, no CLI changes (except D5's required env) |
| FR15, C9, SC8 | D4 state-forwarding seam + enriched tool result + CopilotKit render |
| NFR1 | argon2 hashes; signed expiring JWT; secrets via settings; key never logged (D6) |
| NFR2, SC4 | no-buffering checks at every hop (R4) |
| NFR4 | `just assistant` / `just web` / `just chat`; README quickstart |
| NFR5 | X-Request-ID forwarded from route handlers into `CorrelationIdMiddleware` |
| A1–A6 | one DB (D3/D5); single AG-UI path (D1); no CopilotKit cloud dependency (A5, OQ1) |

## Generated Artifacts

- `specs/copilotkit-frontend/plan.md` (this file)
- To be created during implementation: backend `assistant/db.py`, `assistant/auth/`,
  `assistant/routers/{admin_users,auth,conversations,agent}.py`, `AgentTool`
  subclass + `tools/warehouse.py` state write; `web/` Next.js application; edits to
  `bootstrap.py`, `settings.py`, `middlewares.py`, `pyproject.toml`, `.example.env`,
  `justfile`, `README.md`.

Ready for task breakdown.
