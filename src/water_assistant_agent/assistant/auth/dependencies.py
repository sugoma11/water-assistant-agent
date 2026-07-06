"""The ``current_user`` FastAPI dependency (NFR1, D2, EC5).

``BearerTokenMiddleware`` has already verified the token's signature/expiry and
placed its claims on ``request.state.token_claims`` for every non-public route.
This dependency reads those claims and performs the EC5 per-request existence
check, so a token minted for a since-deleted user is rejected with 401.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from water_assistant_agent.assistant.db import AppUser, get_db

_WWW_AUTH: dict[str, str] = {"WWW-Authenticate": "Bearer"}


def current_user(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> AppUser:
    """Return the verified, still-existing :class:`AppUser` for the request."""
    claims = getattr(request.state, "token_claims", None)
    user_id = claims.get("sub") if isinstance(claims, dict) else None
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers=_WWW_AUTH,
        )
    user = db.get(AppUser, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account no longer exists",
            headers=_WWW_AUTH,
        )
    return user


CurrentUser = Annotated[AppUser, Depends(current_user)]
