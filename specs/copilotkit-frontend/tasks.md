# Tasks: Multi-User Chat Frontend for the Water Assistant

Spec: [`spec.md`](./spec.md) · Plan: [`plan.md`](./plan.md)

Conventions: `[P]` = parallelizable with sibling `[P]` tasks (different files, no
shared edits). Each task names a concrete file/artifact target. Phases follow the
plan's ordering (§Phases/Dependencies): earlier phases unblock later ones; do not start
a phase until its predecessor is green. Phases 1–3 are backend-only and independently
testable with pytest/curl; Phase 4 depends on 1–2, Phase 5 on 3–4. Tests are called out
per phase because the spec's isolation guarantee (NFR3) and success criteria require
API-level verification, not UI inspection.

User stories (from spec): **US1** admin CRUDs accounts via key-protected API · **US2**
provisioned user signs in / out · **US3** answers stream incrementally · **US4** several
conversations with a sidebar (new/switch/rename/delete) · **US5** never see another
user's conversations · **US6** list + history survive reloads/restarts, follow-ups keep
context · **US7** visitor reaches only sign-in.

---

## Phase 1 — Auth & user foundation (backend) — blocks everything

- [x] T001 Add settings in `src/water_assistant_agent/assistant/settings.py`: make
  `jwt_secret_key` (alias `AGENT_JWT_SECRET`) and `session_db_url`
  (`WATER_ASSISTANT_SESSION_DB_URL`) required; add `admin_api_key`
  (`WATER_ASSISTANT_ADMIN_API_KEY`, optional → admin API fails closed) and
  `auth_token_ttl_days` (`WATER_ASSISTANT_AUTH_TOKEN_TTL_DAYS`, default 7, C8); remove
  `user_id`/`WATER_ASSISTANT_USER_ID` (D5, D6, FR2).
- [x] T002 Create `src/water_assistant_agent/assistant/db.py`: SQLAlchemy engine +
  sessionmaker over `session_db_url`; `app_users` and `conversations` table metadata
  (schema per plan Data Model); `metadata.create_all` helper called at startup (OQ3, no
  Alembic yet). Add `sqlalchemy` and `argon2-cffi` as explicit deps in
  `pyproject.toml`. (depends: T001)
- [x] T003 Create `src/water_assistant_agent/assistant/auth/` package: argon2 password
  hash/verify helpers; HS256 JWT mint (`sub`, `email`, `exp` = now + `auth_token_ttl`)
  and verify helpers over `jwt_secret_key`; a `current_user` FastAPI dependency that
  reads the claims `BearerTokenMiddleware` stored on `request.state.token_claims` and
  does the EC5 per-request user-existence check (NFR1, D2). (depends: T002)
- [x] T004 Create `src/water_assistant_agent/assistant/routers/admin_users.py`: FR1
  CRUD (`POST/GET/PATCH/DELETE /admin/users`) authorized only by `X-Admin-API-Key`
  compared with `secrets.compare_digest`; 503 fail-closed when `admin_api_key` unset
  (EC6); 409 on duplicate email (EC4); responses never include password hashes and the
  key is never echoed in errors (NFR1, D6, FR3 — no signup path). DELETE cascades user
  → conversations → ADK sessions (EC5, C5). (depends: T003)
- [x] T005 Create `src/water_assistant_agent/assistant/routers/auth.py`: `POST
  /auth/login` (email+password → `{access_token, expires_at}`, uniform 401 on bad
  credentials) and `GET /auth/me` (current user, session probe) (FR4, C1). (depends:
  T003)
- [x] T006 Update `src/water_assistant_agent/assistant/middlewares.py`: add
  `/auth/login` and the `/admin` prefix to `BearerTokenMiddleware`'s public routes (admin
  has its own key guard); make the middleware mandatory — no skip-with-warning when the
  secret is unset (FR5, FR6, D5). (depends: T001)
- [x] T007 Update `src/water_assistant_agent/assistant/bootstrap.py`: refuse to start
  with a clear error naming the env var when `jwt_secret_key` or `session_db_url` is
  missing; call `create_all` at startup; register the admin and auth routers; drop the
  static demo `user_id` wiring (D5, D6). (depends: T004, T005, T006)
- [x] T008 API tests (pytest + httpx) for Phase 1: admin CRUD; wrong/missing key → 401,
  unconfigured key → 503 fail-closed (FR2, EC6, SC1); duplicate email → 409 (EC4);
  login success + login failure uniform 401; expired-token 401 (EC1 server side); no
  registration route exists (SC6). (depends: T007)

## Phase 2 — Conversations & verified agent identity (backend) — depends on Phase 1

- [x] T009 Create `src/water_assistant_agent/assistant/routers/conversations.py`:
  `GET /conversations` (own only, last-activity desc, FR8); `POST /conversations`
  (`{first_message?}` → `{id, title}`, title truncated from first message or default —
  C6/OQ2, EC8); `PATCH /conversations/{id}` (rename); `DELETE /conversations/{id}`
  (row + ADK session). All queries filter by the verified user id in SQL; foreign or
  unknown id → uniform 404 (FR7, FR13, EC3, NFR3). (depends: T007)
- [x] T010 Add the history + partial endpoints to `conversations.py`:
  `GET /conversations/{id}/messages` returning AG-UI-shaped history by reusing
  `ag_ui_adk`'s `EventTranslator.translate_to_messages` over the stored ADK session
  (FR9 fallback, R1); `POST /conversations/{id}/partial` appending a stopped answer's
  received text as an assistant event via `session_service.append_event` (C7, R2). Both
  ownership-gated per EC3. (depends: T009)
- [x] T011 Create `src/water_assistant_agent/assistant/routers/agent.py`: a custom
  AG-UI endpoint replacing `add_adk_fastapi_endpoint` — ownership check on `thread_id`
  against `conversations` (uniform 404, EC3, blocks the silent-empty-session hole);
  overwrite the forwarded-props identity slot with `request.state.token_claims["sub"]`
  (client-supplied identity discarded, FR6); touch `last_activity_at`; stream SSE
  unbuffered exactly as the upstream endpoint (NFR2). Construct `ADKAgent` with a
  `user_id_extractor` reading that injected slot (raises if absent). (depends: T009)
- [x] T012 Wire Phase 2 routers into `bootstrap.py`: register conversations + custom
  agent endpoint, remove the old `add_adk_fastapi_endpoint` mount and the static
  `user_id` param on `ADKAgent`; ensure the conversations DB and ADK session service
  share the one `session_db_url` engine (D3, A1). (depends: T010, T011)
- [x] T013 API tests for Phase 2: two-user isolation matrix with *valid* credentials —
  sidebar list, foreign `GET/PATCH/DELETE`, foreign `thread_id` on the agent endpoint,
  all uniform-404 (FR13, NFR3, SC2, EC3); unauthenticated rejection of every
  chat/conversation route (FR5, SC6); identity-injection unit test (client-supplied
  props overridden, stub agent) (FR6); deleted-user revocation (EC5); delete-then-repost
  proves no stale-cache resurrection (R5, EC8). (depends: T012)
- [x] T014 Curl SSE smoke run against the agent endpoint: confirm streaming still flows
  unbuffered through the middleware stack (first tokens before completion) and history
  restore works on an empty-`messages` run — `MessagesSnapshotEvent` from the ADK
  session (NFR2, FR9, R4). (depends: T012)

## Phase 3 — FR15 data seam (backend) — depends on Phase 2

- [x] T015 In `src/water_assistant_agent/assistant/tools/warehouse.py`:
  add an ADK-injected `tool_context: ToolContext` param to `query_database_tool`
  (excluded from the LLM-visible schema — prompts untouched) that writes its success
  result (`sql_executed`, `columns`, `rows`, truncation flag) into session state (D4).
  Verify direct/eval callers that pass nothing still work (param inert outside ADK,
  Non-Goal regression). (depends: T012)
- [x] T016 Add an `AgentTool` subclass under `agents/root_agent/` (same tool name
  `text_to_sql_agent`, root prompt untouched) that merges the state-captured query
  result into the returned tool result:
  `{"status","sql","reasoning","results":{"columns","rows"}}`; wire it in place of the
  plain `AgentTool` (D4, FR15, R3). (depends: T015)
- [x] T017 Live agent-run smoke (Phase 3): one real text-to-SQL question, inspect the
  AG-UI event stream for the enriched `text_to_sql_agent` tool result carrying SQL +
  rows, and confirm it survives a history-snapshot replay (FR15, SC8). Regression: one
  standalone eval smoke run confirms the text-to-SQL pipeline itself is unchanged
  (Non-Goal, R3 answer-length watch). (depends: T016)

## Phase 4 — Frontend scaffold — depends on Phases 1–2; [P]-ish with Phase 3

- [x] T018 Scaffold `web/` Next.js app (App Router, TypeScript) with its own toolchain;
  pin `@copilotkit/react-core`, `@copilotkit/react-ui`, `@copilotkit/runtime`,
  `@ag-ui/client`, Next.js versions in `web/package.json` against current docs (OQ1);
  add `web/.env` with `BACKEND_URL`. Confirm existing backend layout/commands untouched
  (FR14, C4, NFR4). (depends: T001)
- [x] T019 `web/app/login/` email+password form (no signup path, FR3) +
  `web/app/api/auth/[...]` login/logout route handlers storing the backend JWT in an
  httpOnly, `SameSite=Lax`, `Secure`-in-prod cookie mirroring `expires_at` (D2, NFR1);
  `web/middleware.ts` route guard sending unauthenticated requests to `/login` (FR5,
  US7). (depends: T018)
- [x] T020 `web/app/api/backend/[...]` authenticated streaming proxy to the FastAPI REST
  routers (copies cookie → `Authorization: Bearer`, no SSE buffering); 401 from any
  proxy call clears the cookie and redirects to `/login` without touching conversation
  state (EC1, EC5, NFR2). (depends: T019)
- [x] T021 `web/app/api/copilotkit/` CopilotKit runtime route: builds `HttpAgent` per
  request pointing at FastAPI `/` with the cookie's Bearer header and forwarded/created
  `X-Request-ID` (NFR5); streams the SSE body unbuffered (NFR2, R4). Plus a minimal
  single-conversation chat pane so a signed-in user can chat with streaming. (depends:
  T020, T012)
- [x] T022 Milestone smoke (SC4): signed-in user sends a message in one conversation and
  the answer's first parts render in the browser before completion — verify no buffering
  at the CopilotKit-runtime and Next-route-handler hops (R4, NFR2, US3). (depends: T021)

## Phase 5 — Chat UX — depends on Phases 3 & 4

- [x] T023 `web/app/(chat)/` sidebar: list/new/rename/delete/switch, last-activity
  order, thread-per-conversation (`threadId` = conversation id); auto-title from first
  message shown, rename available (FR7, FR8, C6, US4). (depends: T022)
- [x] T024 History restore on switch/reload: empty-run `MessagesSnapshotEvent` path;
  verify against the pinned CopilotKit version whether it (re)connects per `threadId`
  and applies the snapshot — if not, fall back to `GET /conversations/{id}/messages` +
  CopilotKit's message-setting API (R1, FR9, US6). (depends: T023)
- [x] T025 Stop control + partial durability: stop button aborts the run (CopilotKit
  stop API), keeps the received partial in the UI, and persists it via
  `POST /conversations/{id}/partial`; verify stop → reload → partial present (C7, FR10,
  R2). (depends: T024)
- [x] T026 `web/components/` renders: markdown for plain answers; a CopilotKit render for
  the `text_to_sql_agent` tool call — `status=success` → SQL as highlighted code block +
  results as a table (note truncation), non-success/missing results → plain markdown
  fallback (FR15, C9, SC8). (depends: T024, T017)
- [x] T027 Edge-case UX: EC2 clear per-message error when the agent backend is
  unreachable (conversation intact, retry works); EC7 refetch conversation list on
  window focus so two tabs converge; EC8 empty conversation never breaks the list or
  switching. (depends: T023)

## Phase 6 — End-to-end validation & docs — depends on Phases 3–5

- [x] T028 Add `just assistant` / `just web` / `just chat` recipes (backend, frontend,
  both together) to the justfile; update `.example.env` (add `AGENT_JWT_SECRET`,
  `WATER_ASSISTANT_SESSION_DB_URL` SQLite dev default, `WATER_ASSISTANT_ADMIN_API_KEY`,
  `WATER_ASSISTANT_AUTH_TOKEN_TTL_DAYS`; retire the demo-identity lines) and `web/.env`;
  README frontend section + admin-API quickstart (create the first user with curl)
  (NFR4, D5, FR14). (depends: T017, T022)
- [x] T029 End-to-end SC walk against `just chat` with two provisioned users: SC1
  (admin key create/deny + sign-in), SC2 (two-user isolation incl. direct API probe),
  SC3 (restart both apps → lists + histories restored), SC4 (streaming), SC5
  (reload-then-follow-up keeps context), SC6 (unauth rejected, no signup reachable), SC7
  (fresh user → empty list), SC8 (text-to-SQL SQL block + results table). (depends:
  T025, T026, T027, T028)
- [x] T030 Run the retrospective skill to review all implemented changes for code
  quality and architectural decisions
  (`specs/copilotkit-frontend/retrospective.md`). (depends: T029)

## Phase 7 — Retrospective Fixes (from `retrospective.md`)

- [x] T031 [R-001, 🟡] Extract a single owned-conversation-or-404 helper and use it in
  both `routers/conversations.py` (`_get_owned_or_404`) and the inlined ownership gate in
  `routers/agent.py:75-84`, so the per-user isolation predicate (FR13/NFR3) lives in one
  place. (depends: T030)
- [x] T032 [R-002, 🟡] Switch `routers/agent.py` from stdlib `logging` to
  `structlog.get_logger`, and log the RUN_ERROR path with
  `logger.exception("ADKAgent run failed", log_context="agent")` so it carries the
  correlation-id contextvars like every other module. (depends: T030)
- [ ] T033 [R-003, 🟢] Add a shared `web/lib/` auth-guard helper (`requireToken()` +
  `unauthorized()`) and use it in `web/app/api/backend/[...path]/route.ts` and
  `web/app/api/copilotkit/route.ts` to remove the duplicated cookie→Bearer / 401 boilerplate.
  (depends: T030)
- [ ] T034 [R-004, 🟢] Update the stale `bootstrap.py` module docstring to reflect mandatory
  auth (D5) and the custom ownership-gated `add_agent_endpoint` (FR6). (depends: T030)
- [ ] T035 [R-005, 🟢] Anchor `middlewares.py` `_PUBLIC_PREFIXES` matching (trailing slash
  or first-segment check) so no future `/admin*`-prefixed route can accidentally bypass JWT.
  (depends: T030)

---

## Summary

- **Total tasks:** 35 (30 delivered T001–T030; T031–T035 are retrospective fixes)
- **Phases:** Auth & user foundation (T001–T008) → Conversations & verified identity
  (T009–T014) → FR15 data seam (T015–T017) → Frontend scaffold (T018–T022) → Chat UX
  (T023–T027) → E2E validation & docs (T028–T030).
- **Per-story coverage:** US1 (admin CRUD) T004, T008; US2 (sign in/out) T005, T019;
  US3 (streaming) T011, T021–T022; US4 (multi-conversation sidebar) T009, T023–T024;
  US5 (isolation) T009, T011, T013; US6 (durability + context) T010, T012, T014,
  T024, T029; US7 (visitor → sign-in only) T006, T019.
- **Parallel opportunities:** Phase 3 (T015–T017) is backend-only and can proceed
  alongside the Phase 4 frontend scaffold once Phase 2 is green (Phase 5's T026 render
  is the only frontend task that depends on Phase 3). Within Phase 1, T008 tests follow
  T007; T018 depends only on T001 so the frontend toolchain can be scaffolded early.
- **Critical path:** T001 → T002 → T003 → T004/T005 → T007 → T009 → T011 → T012 →
  T015 → T016 → T021 → T023 → T024 → T026 → T029 → T030.
