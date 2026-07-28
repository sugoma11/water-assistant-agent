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

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Final

import structlog
from ag_ui.core import (
    EventType,
    RunAgentInput,
    RunErrorEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
)
from ag_ui.encoder import EventEncoder
from ag_ui_adk import ADKAgent
from fastapi import Depends, FastAPI, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from water_assistant_agent.assistant.auth import CurrentUser
from water_assistant_agent.assistant.db import get_db, get_owned_conversation_or_404

logger = structlog.get_logger(__name__)

# Invisible sentinel prefixed onto reasoning text so the frontend can tell a
# thinking-derived assistant message from a real answer and render it as a
# collapsed "Thinking" block. It is a Unicode private-use code point: it never
# occurs in model output and renders as nothing if it ever slips through. The
# same constant tags restored history (see routers/conversations.py) and is
# stripped by the frontend (see web/lib/thinking.ts).
THINKING_MARKER: Final[str] = "\ue000"

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


class _ReasoningRewriter:
    """Turn the ADK reasoning stream into de-duplicated "Thinking" text messages.

    Two problems are solved here, both stemming from ag-ui-adk 0.7 emitting the
    model's thinking as a separate reasoning stream (``REASONING_MESSAGE_*`` →
    an AG-UI message with ``role="reasoning"``):

    1. **CopilotKit drops it.** CopilotKit's runtime (1.62) discards
       ``role="reasoning"`` messages in its agui→gql conversion
       (``if (message.role === "reasoning") continue``), so thinking never reaches
       the chat. We remap each reasoning block onto an ordinary assistant text
       message whose content opens with :data:`THINKING_MARKER` — an invisible
       sentinel the frontend uses to render it as a collapsed "Thinking" block
       (see ``web/components/AssistantMessage.tsx``).

    2. **ADK emits the block twice.** ag-ui-adk 0.7 fixed *intra-stream* reasoning
       duplication (#1645), but only when the reasoning stream is still open
       (its ``was_already_reasoning and not is_partial`` guard). With a non-
       streaming LiteLlm turn, ADK replays the same logical response as two
       ``partial=False`` events (the aggregated + persisted replay behind #1168)
       and the reasoning stream is *closed* between them, so the guard misses it
       and the identical block is emitted twice. We buffer each reasoning block to
       its ``REASONING_MESSAGE_END`` and drop any whose text we already emitted
       this run.

    Buffering (rather than passing deltas straight through) is what makes the
    content-level dedup possible; it costs nothing visible because the Thinking
    block is collapsed by default and never streamed token-by-token.

    Not thread-safe / not reusable: construct one per run (per request).
    """

    def __init__(self) -> None:
        self._buffer: list[str] | None = None  # None ⇒ no reasoning block open
        self._message_id: str | None = None
        self._emitted_texts: set[str] = set()

    def feed(self, event: Any) -> list[Any]:
        """Map one upstream event to zero or more events to forward downstream."""
        etype = event.type
        if etype == EventType.REASONING_MESSAGE_START:
            self._buffer = []
            self._message_id = event.message_id
            return []
        if etype in (
            EventType.REASONING_MESSAGE_CONTENT,
            EventType.REASONING_MESSAGE_CHUNK,
        ):
            if self._buffer is None:  # a chunk may arrive without an explicit start
                self._buffer = []
                self._message_id = event.message_id
            self._buffer.append(getattr(event, "delta", "") or "")
            return []
        if etype == EventType.REASONING_MESSAGE_END:
            return self._flush()
        if etype in (
            EventType.REASONING_START,
            EventType.REASONING_END,
            EventType.REASONING_ENCRYPTED_VALUE,
        ):
            return []  # block-level / encrypted envelopes have no text analogue
        # Any non-reasoning event closes an unterminated reasoning block first so
        # ordering is preserved (e.g. a tool call arriving mid-thought).
        flushed = self._flush() if self._buffer is not None else []
        return [*flushed, event]

    def _flush(self) -> list[Any]:
        """Emit the buffered reasoning block as marked assistant text, or drop it."""
        if self._buffer is None:
            return []
        text = "".join(self._buffer).strip()
        message_id = self._message_id or str(uuid.uuid4())
        self._buffer = None
        self._message_id = None
        if not text or text in self._emitted_texts:
            return []  # empty, or a duplicate replay of an already-shown block
        self._emitted_texts.add(text)
        return [
            TextMessageStartEvent(
                type=EventType.TEXT_MESSAGE_START,
                message_id=message_id,
                role="assistant",
            ),
            TextMessageContentEvent(
                type=EventType.TEXT_MESSAGE_CONTENT,
                message_id=message_id,
                delta=f"{THINKING_MARKER}{text}",
            ),
            TextMessageEndEvent(
                type=EventType.TEXT_MESSAGE_END,
                message_id=message_id,
            ),
        ]


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
            rewriter = _ReasoningRewriter()  # per-run reasoning dedup + tagging
            try:
                async for event in agent.run(run_input):
                    for mapped in rewriter.feed(event):
                        yield encoder.encode(mapped)
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
