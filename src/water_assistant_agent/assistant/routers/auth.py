"""Login + session-probe routes (FR4, C1, D2).

``POST /auth/login`` verifies email+password and mints an HS256 access token;
credential failures return a single uniform 401 (no user-enumeration). ``GET
/auth/me`` echoes the verified current user for the frontend's session probe.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from water_assistant_agent.assistant.auth import (
    CurrentUser,
    mint_access_token,
    verify_password,
)
from water_assistant_agent.assistant.db import AppUser, get_db
from water_assistant_agent.assistant.routers.admin_users import UserResponse, _normalize_email

router = APIRouter(prefix="/auth", tags=["auth"])

_INVALID_CREDENTIALS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid email or password",
    headers={"WWW-Authenticate": "Bearer"},
)


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - not a secret, the OAuth2 token type label
    expires_at: datetime


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> LoginResponse:
    settings = request.app.state.settings
    email = _normalize_email(payload.email)
    user = db.execute(select(AppUser).where(AppUser.email == email)).scalar_one_or_none()
    if user is None or not verify_password(user.password_hash, payload.password):
        raise _INVALID_CREDENTIALS
    token, expires_at = mint_access_token(
        user_id=user.id,
        email=user.email,
        secret=settings.jwt_secret_key,
        ttl_days=settings.auth_token_ttl_days,
    )
    return LoginResponse(access_token=token, expires_at=expires_at)


@router.get("/me", response_model=UserResponse)
def me(user: CurrentUser) -> AppUser:
    return user
