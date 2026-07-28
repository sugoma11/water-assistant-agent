"""Admin user CRUD, authorized only by ``X-Admin-API-Key`` (FR1-FR3, D6).

The key is compared with :func:`secrets.compare_digest`; when unset the whole
router fails closed with 503 (EC6). Responses never include password hashes and
the key is never echoed back in an error (NFR1). Deleting a user cascades to its
conversations (DB ``ON DELETE CASCADE``) and to each ADK session (EC5, C5).
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from water_assistant_agent.assistant.auth import hash_password
from water_assistant_agent.assistant.db import AppUser, get_db

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin/users", tags=["admin"])


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def require_admin(
    request: Request,
    x_admin_api_key: Annotated[str | None, Header(alias="X-Admin-API-Key")] = None,
) -> None:
    """Guard every admin route: fail closed when unconfigured, else compare keys."""
    settings = request.app.state.settings
    if settings.admin_api_key is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin API is not configured",
        )
    print(x_admin_api_key, settings.admin_api_key)
    if x_admin_api_key is None or not secrets.compare_digest(
        x_admin_api_key, settings.admin_api_key
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin API key",
        )


class UserCreate(BaseModel):
    email: str = Field(min_length=1)
    password: str = Field(min_length=1)
    display_name: str | None = None


class UserUpdate(BaseModel):
    email: str | None = Field(default=None, min_length=1)
    password: str | None = Field(default=None, min_length=1)
    display_name: str | None = None


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    display_name: str | None
    created_at: datetime
    updated_at: datetime


def _email_taken(db: Session, email: str, *, exclude_id: str | None = None) -> bool:
    stmt = select(AppUser.id).where(AppUser.email == email)
    if exclude_id is not None:
        stmt = stmt.where(AppUser.id != exclude_id)
    return db.execute(stmt).first() is not None


@router.post(
    "",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
def create_user(
    payload: UserCreate,
    db: Annotated[Session, Depends(get_db)],
) -> AppUser:
    email = _normalize_email(payload.email)
    if _email_taken(db, email):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already exists")
    user = AppUser(
        email=email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:  # concurrent insert of the same email
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already exists"
        ) from exc
    db.refresh(user)
    return user


@router.get("", response_model=list[UserResponse], dependencies=[Depends(require_admin)])
def list_users(db: Annotated[Session, Depends(get_db)]) -> list[AppUser]:
    return list(db.execute(select(AppUser).order_by(AppUser.created_at)).scalars())


def _get_user_or_404(db: Session, user_id: str) -> AppUser:
    user = db.get(AppUser, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.patch(
    "/{user_id}", response_model=UserResponse, dependencies=[Depends(require_admin)]
)
def update_user(
    user_id: str,
    payload: UserUpdate,
    db: Annotated[Session, Depends(get_db)],
) -> AppUser:
    user = _get_user_or_404(db, user_id)
    if payload.email is not None:
        email = _normalize_email(payload.email)
        if _email_taken(db, email, exclude_id=user.id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Email already exists"
            )
        user.email = email
    if payload.password is not None:
        user.password_hash = hash_password(payload.password)
    if payload.display_name is not None:
        user.display_name = payload.display_name
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already exists"
        ) from exc
    db.refresh(user)
    return user


async def _delete_adk_sessions(request: Request, user_id: str, session_ids: list[str]) -> None:
    """Best-effort deletion of the user's ADK sessions (EC5, C5)."""
    session_service = getattr(request.app.state, "session_service", None)
    if session_service is None:
        return
    app_name = request.app.state.settings.app_name
    for session_id in session_ids:
        try:
            await session_service.delete_session(
                app_name=app_name, user_id=user_id, session_id=session_id
            )
        except Exception:  # noqa: BLE001 - stale/missing sessions must not block deletion
            logger.warning(
                "Failed to delete ADK session during user delete",
                session_id=session_id,
                log_context="admin",
            )


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
async def delete_user(
    user_id: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    user = _get_user_or_404(db, user_id)
    session_ids = [conv.id for conv in user.conversations]
    await _delete_adk_sessions(request, user.id, session_ids)
    db.delete(user)  # cascades to conversation rows (ON DELETE CASCADE)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
