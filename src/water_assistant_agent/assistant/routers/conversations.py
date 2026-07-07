"""Conversation metadata CRUD, owner-scoped (FR7, FR8, FR13, EC3, EC8, NFR3).

Every query filters by the verified user's id *in SQL*, so a foreign or unknown
conversation id is indistinguishable from a nonexistent one — all yield the same
uniform 404 (EC3, server-side isolation). A conversation ``id`` *is* the AG-UI
thread id and ADK session id (D3); deleting a conversation drops the metadata row
and the backing ADK session (C5).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from ag_ui_adk import EventTranslator
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from google.adk.events import Event
from google.genai import types
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from water_assistant_agent.assistant.auth import CurrentUser
from water_assistant_agent.assistant.db import (
    Conversation,
    get_db,
    get_owned_conversation_or_404,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/conversations", tags=["conversations"])

# Title derivation is truncation, not an LLM summary (OQ2, C6): keeps the agent
# untouched and adds no cost.
_TITLE_MAX_LEN = 60
_DEFAULT_TITLE = "New conversation"


def _derive_title(first_message: str | None) -> str:
    """Server-side title from the optional first message (C6, EC8)."""
    stripped = (first_message or "").strip()
    if not stripped:
        return _DEFAULT_TITLE
    if len(stripped) <= _TITLE_MAX_LEN:
        return stripped
    return stripped[:_TITLE_MAX_LEN].rstrip() + "…"


class ConversationCreate(BaseModel):
    first_message: str | None = None


class ConversationRename(BaseModel):
    title: str


class ConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    created_at: datetime
    last_activity_at: datetime


class PartialAppend(BaseModel):
    content: str


class MessagesResponse(BaseModel):
    messages: list[dict[str, Any]]


@router.get("", response_model=list[ConversationResponse])
def list_conversations(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> list[Conversation]:
    """List the caller's own conversations, most-recently-active first (FR8)."""
    return list(
        db.execute(
            select(Conversation)
            .where(Conversation.user_id == user.id)
            .order_by(Conversation.last_activity_at.desc())
        ).scalars()
    )


@router.post("", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
def create_conversation(
    payload: ConversationCreate,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> Conversation:
    """Create an (empty) conversation owned by the caller (FR7, EC8)."""
    conv = Conversation(user_id=user.id, title=_derive_title(payload.first_message))
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


@router.patch("/{conversation_id}", response_model=ConversationResponse)
def rename_conversation(
    conversation_id: str,
    payload: ConversationRename,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> Conversation:
    """Rename a conversation the caller owns; foreign/unknown id → 404 (FR7, EC3)."""
    conv = get_owned_conversation_or_404(db, user.id, conversation_id)
    conv.title = _derive_title(payload.title)
    db.commit()
    db.refresh(conv)
    return conv


async def _delete_adk_session(request: Request, user_id: str, session_id: str) -> None:
    """Best-effort deletion of the backing ADK session (C5)."""
    session_service = getattr(request.app.state, "session_service", None)
    if session_service is None:
        return
    app_name = request.app.state.settings.app_name
    try:
        await session_service.delete_session(
            app_name=app_name, user_id=user_id, session_id=session_id
        )
    except Exception:  # noqa: BLE001 - a stale/missing session must not block deletion
        logger.warning(
            "Failed to delete ADK session during conversation delete",
            session_id=session_id,
            log_context="conversations",
        )


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: str,
    request: Request,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    """Delete a conversation row and its ADK session (EC3, C5)."""
    conv = get_owned_conversation_or_404(db, user.id, conversation_id)
    await _delete_adk_session(request, user.id, conv.id)
    db.delete(conv)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _get_adk_session(request: Request, user_id: str, session_id: str) -> Any:
    """Fetch the backing ADK session (keyed by the verified user id), or ``None``."""
    session_service = request.app.state.session_service
    app_name = request.app.state.settings.app_name
    return await session_service.get_session(
        app_name=app_name, user_id=user_id, session_id=session_id
    )


@router.get("/{conversation_id}/messages", response_model=MessagesResponse)
async def get_messages(
    conversation_id: str,
    request: Request,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> MessagesResponse:
    """Return the conversation's AG-UI-shaped message history (FR9 fallback, R1).

    Reuses ``ag_ui_adk``'s :meth:`EventTranslator.translate_to_messages` over the
    stored ADK session events — the same translation the empty-run
    ``MessagesSnapshotEvent`` uses — so the REST fallback and the streaming path
    agree. Ownership-gated per EC3.
    """
    get_owned_conversation_or_404(db, user.id, conversation_id)
    session = await _get_adk_session(request, user.id, conversation_id)
    adk_events = list(getattr(session, "events", []) or [])
    messages = await EventTranslator().translate_to_messages(
        adk_events=adk_events,
        thread_id=conversation_id,
        run_id=str(uuid.uuid4()),
    )
    return MessagesResponse(
        messages=[m.model_dump(by_alias=True, exclude_none=True) for m in messages]
    )


@router.post("/{conversation_id}/partial", status_code=status.HTTP_201_CREATED)
async def append_partial(
    conversation_id: str,
    payload: PartialAppend,
    request: Request,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    """Persist a stopped answer's received text as an assistant event (C7, R2).

    When the client aborts an SSE run the in-flight assistant turn may never be
    committed to the ADK session; this appends the partial text so it survives a
    reload. Ownership-gated per EC3; 404 when no ADK session exists yet (EC8).
    """
    conv = get_owned_conversation_or_404(db, user.id, conversation_id)
    session = await _get_adk_session(request, user.id, conversation_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        )
    app_name = request.app.state.settings.app_name
    event = Event(
        invocation_id=str(uuid.uuid4()),
        author=app_name,
        content=types.Content(role="model", parts=[types.Part(text=payload.content)]),
    )
    await request.app.state.session_service.append_event(session, event)
    conv.last_activity_at = datetime.now(UTC)
    db.commit()
    return Response(status_code=status.HTTP_201_CREATED)
