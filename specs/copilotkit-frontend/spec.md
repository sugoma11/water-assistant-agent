# Feature Specification: Multi-User Chat Frontend for the Water Assistant

## Overview / Context

The water assistant is an agent backend: a FastAPI application hosting a Google
ADK agent that answers water-management questions (green-roof sensor data,
text-to-SQL over a warehouse). The backend already exposes the agent through
the AG-UI protocol as a single streaming endpoint. Today it has no user
interface, every request runs under one fixed demo identity, and conversation
history survives restarts only when a session database is configured.

This feature adds a web chat frontend: a simple multi-conversation chat in the
style of ChatGPT, built with CopilotKit and speaking AG-UI to the existing
backend endpoint (both named here as constraints set by the project owner; the
requirements below stay implementation-neutral). The frontend is multi-user.
An administrator provisions accounts through a key-protected user-management
API — there is no self-signup. Users sign in with the credentials they were
given, hold any number of conversations, and see only their own. Frontend and
backend live together in this repository as a monorepo.

## Goals

- Give users a ChatGPT-like chat over the existing agent: several
  conversations per user, a sidebar to manage them, and answers that stream in
  as they are produced.
- Provision and manage user accounts exclusively through an administrator's
  user-management API, authorized by a single API key from environment
  configuration.
- Let provisioned users sign in with email and password, and sign out.
- Enforce per-user conversation isolation on the server: a user can never
  list, read, or continue another user's conversations.
- Carry a verified user identity on every request to the agent backend,
  replacing the fixed demo identity.
- Make conversations durable: list and full history survive browser reloads
  and restarts of every component.
- Host the frontend in the same repository as the backend, without disturbing
  the existing Python package, commands, or workflows.

## Non-Goals

- Self-signup or registration of any kind, password reset, email
  verification, and external identity providers (SSO, OAuth).
- An admin web console — user management is API-only.
- Changing what the agent does: the text-to-SQL pipeline, prompts, models,
  and answer quality are untouched.
- Rich generative UI or human-in-the-loop widgets beyond a plain chat
  (rendering markdown in answers is expected; custom interactive components
  are not). One custom render IS in scope: the text-to-SQL output display —
  produced SQL and query results (FR15, C9).
- Regenerating an assistant answer and editing or forking past messages
  (stopping an in-progress answer IS in scope — FR10, C7).
- Mobile applications.
- Rate limiting, usage quotas, or billing.
- Production deployment infrastructure (containers, TLS termination,
  horizontal scaling).

## User Stories / Scenarios

1. **As an administrator**, I can create, list, update, and delete user
   accounts by calling a user-management API with my API key, so I control
   exactly who can use the assistant.
2. **As a provisioned user**, I can sign in with the email and password the
   administrator gave me, and sign out when I am done.
3. **As a user**, I can ask a question and watch the answer appear
   incrementally, instead of waiting for the complete response.
4. **As a user**, I can hold several conversations: start a new one, switch
   between them in a sidebar, rename one, and delete one I no longer need.
5. **As a user**, I never see another user's conversations — not in my
   sidebar, and not by requesting their conversation directly.
6. **As a user**, when I reload the page or the servers restart, my
   conversation list and full message history are back, and follow-up
   questions still use the earlier context.
7. **As a visitor without an account**, I can reach only the sign-in screen;
   every chat and conversation function is closed to me.

## Functional Requirements

- FR1. The system MUST provide a user-management API with four operations:
  create a user (unique email plus initial password), list users, update a
  user (including setting a new password), and delete a user.
- FR2. The user-management API MUST be authorized solely by an admin API key
  supplied through environment configuration. Requests with a missing or
  wrong key MUST be denied. If no key is configured, the API MUST refuse all
  requests rather than default open (EC6).
- FR3. The system MUST NOT offer any self-registration path, in the UI or the
  API. Accounts come into existence only through FR1.
- FR4. Provisioned users MUST be able to sign in with email and password and
  to sign out. Signed-in sessions MUST expire after a configured lifetime,
  defaulting to 7 days (C8); an expired session requires signing in again
  (EC1).
- FR5. All chat and conversation functionality MUST require a signed-in
  user. Unauthenticated requests to any chat or conversation operation MUST
  be rejected.
- FR6. Every request reaching the agent backend MUST carry a verified user
  identity, and the agent backend MUST reject requests whose identity is
  missing or cannot be verified. The current fixed demo identity is removed.
- FR7. A user MUST be able to start a new conversation, switch to any of
  their existing conversations, rename a conversation, and delete a
  conversation.
- FR8. The conversation sidebar MUST list only the requesting user's
  conversations, ordered by most recent activity, each with a title. A new
  conversation's initial title is derived automatically from the user's first
  message (e.g., truncated text or a short generated summary); the user can
  rename it afterwards (FR7, C6).
- FR9. Selecting a conversation MUST load its full message history into the
  chat view.
- FR10. Assistant responses MUST stream to the browser incrementally: the
  first parts of an answer render before the answer is complete. The user
  MUST be able to stop an in-progress answer; the partial answer already
  received is kept in the conversation (C7). Regenerating answers and
  editing past messages are out of scope.
- FR11. Follow-up questions MUST be answered using the conversation's earlier
  messages as context. The agent's memory is keyed per user and per
  conversation, so each conversation maps to exactly one agent session.
- FR12. Conversations — their metadata and their messages — MUST be stored
  server-side and survive restarts of every component.
- FR13. Conversation access control MUST be enforced server-side, not by UI
  filtering. A request naming a conversation the requester does not own MUST
  be denied without revealing whether it exists (EC3).
- FR14. The frontend and the backend MUST live in this repository, each with
  its own toolchain. Existing backend workflows — the Python package layout,
  its commands, and its configuration — MUST keep working unchanged.
- FR15. When an assistant answer includes output from the text-to-SQL agent,
  the chat MUST render that output as distinct, readable elements within the
  assistant's message: the produced SQL statement (as formatted code) and the
  query results (as a table or equivalent structured view), not merely as
  undifferentiated text. Answers without text-to-SQL output render as plain
  markdown chat (C9).

## Non-Functional Requirements

- NFR1. **Security** — Passwords are stored only as strong one-way hashes.
  Auth tokens are signed and expire. The admin API key and every other secret
  come from environment configuration, following the backend's existing
  settings convention; the key is never written to logs.
- NFR2. **Streaming responsiveness** — No component between the agent and the
  browser buffers a complete response before forwarding it.
- NFR3. **Isolation, verifiable** — The per-user isolation guarantee holds at
  the API level and is verified by direct requests with another user's valid
  credentials, not only by inspecting the UI.
- NFR4. **Developer experience** — Each application starts for development
  with one documented command, and the pair can be started together for a
  working end-to-end setup. Existing Python commands behave exactly as
  before.
- NFR5. **Observability** — Requests that flow from the frontend into the
  backend fit the backend's existing correlation-id and structured-logging
  setup, so one chat request can be followed across components.

## Data / Entities

- **User account** — unique email, password hash, optional display name,
  created/updated timestamps. Created and maintained only by the
  administrator (FR1).
- **Conversation** — identifier, owning user, title, created and
  last-activity timestamps. Owned by exactly one user.
- **Message** — role (user or assistant), content, and position within its
  conversation.
- **Agent session** — the backend's existing per-user, per-conversation
  memory; each conversation maps to exactly one agent session (FR11).
- **Auth session/token** — proof of a signed-in user, with an expiry.
- **Admin API key** — one secret from environment configuration that
  authorizes the user-management API (FR2).

## Assumptions

- A1. A SQL database is available for user accounts and conversation data —
  the same database intended for the backend's existing session-persistence
  setting.
- A2. The backend's existing AG-UI streaming endpoint remains the single
  interface to the agent; the frontend introduces no second path to it.
- A3. One trusted administrator operates user management with one shared API
  key; there are no admin accounts, roles, or audit requirements.
- A4. A single running instance of each component is acceptable; nothing
  requires horizontal scaling.
- A5. The open-source CopilotKit feature set (conversation threads, per-user
  thread listing, self-hosted persistence) suffices; no enterprise or hosted
  cloud dependency is introduced.
- A6. Users are a small, trusted, internal audience; abuse protection beyond
  authentication is not needed.

## Success Criteria

- SC1. The administrator creates a user through the API with the correct key
  and the call succeeds; the same call with a missing or wrong key is
  denied; the created user can then sign in. (FR1, FR2, FR4)
- SC2. Two users each hold at least two conversations. Neither sees the
  other's conversations in the sidebar, and a direct API request for the
  other's conversation id, made with valid credentials, is denied. (FR8,
  FR13)
- SC3. After restarting both applications, both users' conversation lists
  and full histories are restored. (FR12)
- SC4. While an answer is being produced, its first parts are visible in the
  chat before the answer completes. (FR10)
- SC5. A follow-up question that only makes sense given the previous exchange
  is answered correctly, in the same conversation, after a page reload.
  (FR9, FR11)
- SC6. Unauthenticated requests to chat and conversation operations are
  rejected, and no registration path is reachable in the UI or the API.
  (FR3, FR5)
- SC7. A freshly provisioned user signs in to an empty conversation list.
  (FR8)
- SC8. A question answered through the text-to-SQL agent shows, in the
  assistant's message, the produced SQL rendered as formatted code and the
  query results rendered as a structured view, visually distinct from the
  surrounding prose. (FR15)

## Edge Cases / Error Handling

- EC1. **Session expires mid-use** — the user is sent to sign-in cleanly; no
  stored conversation data is lost, and after signing in again the
  conversations are intact.
- EC2. **Agent backend unreachable** — the chat surfaces a clear error for
  the failed message; the conversation and its history remain intact, and
  retrying once the backend is back works.
- EC3. **Foreign or unknown conversation id** — a request for a conversation
  the requester does not own is denied with the same response as for one
  that does not exist, so ids leak nothing.
- EC4. **Duplicate email on create** — the administrator gets a clear error;
  no account is created or altered.
- EC5. **User deleted while signed in** — the deleted user's access is
  revoked promptly: an existing session cannot keep chatting. The user's
  conversations are removed with the account (C5).
- EC6. **Admin API key not configured** — the user-management API refuses
  every request with a clear error; it must not fall open.
- EC7. **Same user in two tabs** — both tabs work; conversation changes made
  in one appear in the other, at the latest on refresh, without corrupting
  either.
- EC8. **Conversation started but never messaged** — an empty conversation
  never breaks the list or switching; it either is not persisted or shows as
  empty.

## Clarifications (resolved)

- C1. **Authentication method** — self-managed email-and-password sign-in
  against the project's own user store; no external identity provider.
  (FR4, FR6)
- C2. **User provisioning** — no self-signup or registration of any kind;
  a user CRUD API authorized by an admin API key from an environment
  variable, as directed by the project owner. (FR1–FR3)
- C3. **Frontend stack and protocol** — CopilotKit as the frontend framework,
  talking AG-UI to the backend's existing endpoint; a constraint set by the
  project owner, recorded here because the requirements themselves stay
  implementation-neutral. (FR10, A2, A5)
- C4. **Repository layout** — one monorepo: a new web application added
  alongside the untouched Python package; workspace mechanics are an
  implementation-plan concern. (FR14)
- C5. **Deleted user's conversations** — deleted with the account; there is
  no retention or hand-over requirement. (EC5)

### Clarifications added 2026-07-03

- C6. **Initial conversation title** — derived automatically from the user's
  first message (truncated text or a short generated summary); manual rename
  remains available. (FR7, FR8)
- C7. **In-chat controls** — send a message and stop an in-progress answer,
  keeping the partial answer; no regenerate, no editing or forking of past
  messages. (FR10, Non-Goals)
- C8. **Session lifetime** — configurable, defaulting to 7 days; acceptable
  for the small trusted audience (A6) because deleting a user still revokes
  access promptly (EC5). (FR4)
- C9. **Text-to-SQL output rendering** — the chat is not 100% standard: when
  the text-to-SQL agent produces output, the assistant's message renders the
  produced SQL and the query results as distinct structured elements (code
  block, table), per project owner direction. This is the single exception to
  the plain-chat Non-Goal. (FR15)
