# Retrospective: Multi-User Chat Frontend (copilotkit-frontend)

Spec: [`spec.md`](./spec.md) · Plan: [`plan.md`](./plan.md) · Tasks: [`tasks.md`](./tasks.md)

## Summary

**Clean, with a few small tidy-ups.** The branch delivers the full plan: a mandatory-auth
FastAPI backend (admin/auth/conversations routers, custom ownership-gated AG-UI endpoint,
FR15 data seam) plus a thin Next.js/CopilotKit frontend, all matching the plan's D1–D6
decisions. Code is well-documented (every module ties back to its FR/EC/decision), typed,
and layered as designed; the isolation guarantee is enforced in SQL and verified end-to-end
(T029). The findings below are quality nits — one security-critical predicate is duplicated
and one module deviates from the repo's structlog convention — none are correctness bugs
and none block the feature.

## Code Quality Findings

### 🟡 MEDIUM

**R-001 — Ownership 404 gate duplicated (DRY, security-critical).**
`routers/agent.py:75-84` inlines the exact `select(Conversation).where(id ==, user_id ==)`
+ uniform-404 that `routers/conversations.py:73-85` already exposes as
`_get_owned_or_404`. This predicate *is* the per-user isolation boundary (FR13/NFR3); two
copies can drift, and a change to one (e.g. adding a soft-delete filter) could silently
leave the other permissive. **Fix:** extract one shared `get_owned_conversation_or_404(db,
user_id, conversation_id)` helper (e.g. in `db.py` or a small conversations service module)
and call it from both the router and the agent endpoint.

**R-002 — `routers/agent.py` uses stdlib `logging`, not structlog (logging discipline).**
`agent.py:23,38` do `import logging` / `logging.getLogger(__name__)`, and `:100` logs
`logger.error("ADKAgent error: %s", agent_error, exc_info=True)`. Every other new module
(`conversations`, `admin_users`, `middlewares`, `bootstrap`) uses `structlog.get_logger`
with keyword args, and the repo convention is `logger.exception(...)` inside `except`.
Consequences: this log line is emitted outside the structlog pipeline, so it loses the
correlation-id contextvars and structured fields the rest of the service carries — exactly
the RUN_ERROR path where request correlation matters most. **Fix:** switch to
`structlog.get_logger(__name__)` and `logger.exception("ADKAgent run failed",
log_context="agent")`.

### 🟢 LOW

**R-003 — Auth-guard boilerplate duplicated across the two Next.js route handlers (DRY).**
`web/app/api/backend/[...path]/route.ts:18-34` and `web/app/api/copilotkit/route.ts:26-46`
each re-implement "read cookie → if absent return `{error:"Not authenticated",
redirect:"/login"}` 401 → else set `Authorization: Bearer <token>`". **Fix:** a shared
`web/lib/` helper (`requireToken()` returning the token or a 401 `NextResponse`, plus an
`unauthorized()` builder) removes the copy and keeps the 401 contract identical.

**R-004 — Stale `bootstrap.py` module docstring after the mandatory-auth pivot.**
`bootstrap.py:1-14` still describes "optional JWT-auth middleware" and "the AG-UI ADK
endpoint mounted at `/`", but D5 made auth mandatory (`_require_service_config`) and the
endpoint is now the custom ownership-gated `add_agent_endpoint`, not
`add_adk_fastapi_endpoint`. **Fix:** update the docstring to reflect D5/FR6 so it doesn't
mislead the next reader.

**R-005 — `_PUBLIC_PREFIXES = ("/admin",)` matched with bare `startswith` (defensive).**
`middlewares.py:28,107`: any future path *starting with* `/admin` (e.g. `/administer`)
would bypass JWT verification. No live exposure today — the only `/admin*` routes are the
key-guarded admin router — but matching `"/admin/"` (or an exact first-segment check) would
prevent an accidental future public route. **Fix:** anchor the prefix to a trailing slash
or match on the path's first segment.

## Architectural Observations

- **Layer boundaries are respected.** Identity is injected server-side only
  (`agent.py:_inject_verified_identity` + `extract_verified_user_id`), the client-supplied
  slot is discarded, and every conversation query filters by the verified id in SQL — the
  FR6/FR13 contract holds with no client-trusted path. Verified live in T029 (foreign
  `thread_id` → 404 at both the conversations router and the agent endpoint).
- **The one-DB decision (A1/D3) is cleanly realized.** `db.to_sync_url` deriving the sync
  driver from ADK's async URL is a tidy seam that keeps the ORM and ADK's session service
  on the same database without a second config knob. Good isolation for tests (engine on
  `app.state`, not a module global).
- **FR15 seam is minimal and correct.** The `tool_context` state write is inert for
  direct/eval callers (`tool_context is None`), so the Non-Goal regression holds, and
  `_enrich_tool_result` keys off *captured query state* rather than the model's output
  format — robust to the model answering in prose (observed in T029, where the tool result
  correctly carried `results.rows` even though the model narrated its reasoning). The
  phased plan did **not** produce redundant abstractions here.
- **R3 watch (plan risk) — noted, not a code issue.** T029 observed the target model
  streaming chain-of-thought into the visible answer; that is a model/prompt-format concern
  tracked in `e2e-validation.md`, orthogonal to this branch's integration code.

## Metrics

| Metric | Value |
|---|---|
| Files changed | 52 (44 new, 8 modified) |
| Backend source added | ~1,366 lines (`src/**/*.py`) |
| Frontend source added | ~1,307 lines (`web/**/*.{ts,tsx}`) |
| Tests added | ~516 lines (3 phase suites + conftest) |
| Findings | 🔴 0 · 🟡 2 · 🟢 3 |

Two 🟡 findings are actionable; see the **Retrospective Fixes** phase appended to
`tasks.md` (T031–T035).
