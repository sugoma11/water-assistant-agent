"""HS256 access-token minting over the shared JWT secret (D2).

Verification lives in ``BearerTokenMiddleware`` (it decodes every non-public
request and stores the claims on ``request.state.token_claims``); this module only
mints. Tokens are stateless — revocation on user deletion is handled by the
per-request existence check in :mod:`.dependencies` (EC5), not by a token table.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final

import jwt

_ALGORITHM: Final[str] = "HS256"


def mint_access_token(
    *,
    user_id: str,
    email: str,
    secret: str,
    ttl_days: int,
) -> tuple[str, datetime]:
    """Mint a signed token for *user_id* and return ``(token, expires_at)``.

    Claims: ``sub`` (user id — the identity used for ADK session keying), ``email``,
    ``iat`` and ``exp`` (now + *ttl_days*).
    """
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=ttl_days)
    payload = {
        "sub": user_id,
        "email": email,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(payload, secret, algorithm=_ALGORITHM)
    return token, expires_at
