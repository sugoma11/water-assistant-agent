"""Custom AG-UI endpoint: ownership-gated, verified-identity streaming (FR6, EC3, NFR2).

Replaces ``ag_ui_adk.add_adk_fastapi_endpoint``. Before streaming it

* rejects a ``thread_id`` that does not name a conversation owned by the verified
  user with the same uniform 404 as an unknown one (EC3) — this also closes the
  silent-empty-session hole, where posting a foreign thread id would otherwise make
  ``ADKAgent`` create a fresh empty session under the requester's identity;
* overwrites whatever identity the client put in the forwarded props with the
  server-verified user id from the JWT (FR6 — client-supplied identity is
  discarded, which is what makes the identity *verified*);
* touches the conversation's ``last_activity_at`` so the sidebar orders by recency
  (FR8).

The SSE body is then streamed exactly as the upstream endpoint does, unbuffered
(NFR2). ``ADKAgent`` is constructed (in ``bootstrap``) with
:func:`extract_verified_user_id` as its ``user_id_extractor``, so the session is
keyed by the injected id and never by anything the client sent.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Final

import structlog
from ag_ui.core import EventType, RunAgentInput, RunErrorEvent
from ag_ui.encoder import EventEncoder
from ag_ui_adk import ADKAgent
from fastapi import Depends, FastAPI, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from water_assistant_agent.assistant.auth import CurrentUser
from water_assistant_agent.assistant.db import get_db, get_owned_conversation_or_404

logger = structlog.get_logger(__name__)

# Forwarded-props slot carrying the server-verified user id. The endpoint writes
# ``token_claims["sub"]`` here; :func:`extract_verified_user_id` (the ADKAgent
# ``user_id_extractor``) reads it. This module owns the identity contract.
VERIFIED_USER_ID_PROP: Final[str] = "user_id"


def extract_verified_user_id(input_data: RunAgentInput) -> str:
    """ADK ``user_id_extractor``: read the injected verified id, or raise (FR6)."""
    props = input_data.forwarded_props
    user_id = props.get(VERIFIED_USER_ID_PROP) if isinstance(props, dict) else None
    if not user_id:
        raise ValueError("Verified user id missing from forwarded props")
    return str(user_id)


def _inject_verified_identity(input_data: RunAgentInput, user_id: str) -> RunAgentInput:
    """Return a copy of *input_data* with the verified id in the identity slot (FR6)."""
    props = input_data.forwarded_props
    new_props: dict[str, Any] = dict(props) if isinstance(props, dict) else {}
    new_props[VERIFIED_USER_ID_PROP] = user_id
    return input_data.model_copy(update={"forwarded_props": new_props})


def add_agent_endpoint(app: FastAPI, agent: ADKAgent, path: str = "/") -> None:
    """Mount the ownership-gated, identity-injecting AG-UI endpoint at *path*."""

    @app.post(path)
    async def agent_endpoint(
        input_data: RunAgentInput,
        request: Request,
        user: CurrentUser,
        db: Annotated[Session, Depends(get_db)],
    ) -> StreamingResponse:
        # Ownership gate (EC3): the thread must name a conversation this user owns.
        # Foreign or unknown ids get the same uniform 404 as a nonexistent one. The
        # predicate is shared with the conversations router so it cannot drift (R-001).
        conv = get_owned_conversation_or_404(db, user.id, input_data.thread_id)
        conv.last_activity_at = datetime.now(UTC)  # FR8 recency ordering
        db.commit()

        # FR6: discard whatever identity the client supplied; key the run by the
        # server-verified id only.
        run_input = _inject_verified_identity(input_data, user.id)

        accept_header = request.headers.get("accept")
        encoder = EventEncoder(accept=accept_header)

        async def event_generator() -> Any:
            try:
                async for event in agent.run(run_input):
                    yield encoder.encode(event)
            except Exception as agent_error:  # noqa: BLE001 - surface as a RUN_ERROR
                logger.exception("ADKAgent run failed", log_context="agent")
                error_event = RunErrorEvent(
                    type=EventType.RUN_ERROR,
                    message=f"Agent execution failed: {agent_error}",
                    code="AGENT_ERROR",
                )
                yield encoder.encode(error_event)

        return StreamingResponse(
            event_generator(), media_type=encoder.get_content_type()
        )
