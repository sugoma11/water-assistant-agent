"""Conversation metadata CRUD, owner-scoped (FR7, FR8, FR13, EC3, EC8, NFR3).

Every query filters by the verified user's id *in SQL*, so a foreign or unknown
conversation id is indistinguishable from a nonexistent one — all yield the same
uniform 404 (EC3, server-side isolation). A conversation ``id`` *is* the AG-UI
thread id and ADK session id (D3); deleting a conversation drops the metadata row
and the backing ADK session (C5).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from water_assistant_agent.assistant.auth import CurrentUser
from water_assistant_agent.assistant.db import Conversation, get_db

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


def _get_owned_or_404(db: Session, user_id: str, conversation_id: str) -> Conversation:
    """Return the conversation only if *user_id* owns it; else uniform 404 (EC3)."""
    conv = db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
    ).scalar_one_or_none()
    if conv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        )
    return conv


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
    conv = _get_owned_or_404(db, user.id, conversation_id)
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
    conv = _get_owned_or_404(db, user.id, conversation_id)
    await _delete_adk_session(request, user.id, conv.id)
    db.delete(conv)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
