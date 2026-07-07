"""ASGI middlewares for the FastAPI application.

Ported from core-agent ``middlewares.py`` — request logging with correlation-ID
context and mandatory JWT bearer auth. ``bootstrap`` always wires the bearer
middleware (the service refuses to start without a JWT secret, D5); every route
except ``_PUBLIC_ROUTES`` / ``_PUBLIC_PREFIXES`` requires a valid token.
"""

import time
from typing import Any, Final, cast

import jwt
import structlog
from asgi_correlation_id.context import correlation_id
from fastapi import HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp

_ALGORITHM: Final[str] = "HS256"
# Exact paths that skip bearer-token verification. ``/auth/login`` mints the
# token; the admin API (below) guards itself with its own X-Admin-API-Key.
_PUBLIC_ROUTES: Final[frozenset[str]] = frozenset(
    ("/health", "/docs", "/openapi.json", "/auth/login")
)
# Path prefixes that skip bearer-token verification. ``/admin`` has its own key
# guard (see ``routers.admin_users.require_admin``), so it is exempt from JWT.
# Matched on a path-segment boundary (see :func:`_is_public`) so an unrelated
# route like ``/administer`` can never accidentally inherit the exemption.
_PUBLIC_PREFIXES: Final[tuple[str, ...]] = ("/admin",)


def _is_public(path: str) -> bool:
    """Return whether *path* skips bearer-token verification.

    Prefixes match only on a segment boundary — ``/admin`` covers ``/admin`` and
    ``/admin/users`` but not ``/administer`` — so no future ``/admin``-prefixed
    route can bypass JWT by accident (defensive).
    """
    if path in _PUBLIC_ROUTES:
        return True
    return any(
        path == prefix or path.startswith(prefix + "/") for prefix in _PUBLIC_PREFIXES
    )


def _www_auth_header() -> dict[str, str]:
    return {"WWW-Authenticate": "Bearer"}


logger = structlog.get_logger(__name__)


class LoggingMiddleware(BaseHTTPMiddleware):
    """Log every HTTP request with timing and correlation-ID context."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        structlog.contextvars.clear_contextvars()
        request_id = correlation_id.get()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        start_time = time.perf_counter_ns()
        response: Response = Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("Uncaught exception", log_context="error")
            raise
        self._log_metrics(request, response, start_time, request_id)
        return response

    def _log_metrics(
        self,
        request: Request,
        response: Response,
        start_time_ns: int,
        request_id: str | None,
    ) -> None:
        process_time = time.perf_counter_ns() - start_time_ns
        path = request.url.path
        query = request.url.query
        if query:
            path = f"{path}?{query}"
        client_host = request.client.host if request.client else "unknown"
        client_port = request.client.port if request.client else 0
        logger.debug(
            (
                f"{client_host}:{client_port} - "
                f"'{request.method} {path} HTTP/{request.scope['http_version']}' "
                f"{response.status_code}"
            ),
            http={
                "url": str(request.url),
                "status_code": response.status_code,
                "method": request.method,
                "request_id": request_id,
                "version": request.scope["http_version"],
            },
            network={"client": {"ip": client_host, "port": client_port}},
            duration=process_time,
            log_context="access",
        )
        response.headers["X-Process-Time"] = str(process_time / 10**9)


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Validate JWT bearer tokens for all non-public routes."""

    def __init__(self, app: ASGIApp, jwt_secret_key: str) -> None:
        super().__init__(app)
        self._jwt_secret_key = jwt_secret_key

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if _is_public(request.url.path):
            return await call_next(request)
        # This middleware runs outside FastAPI's ExceptionMiddleware, so a raised
        # HTTPException would surface as a 500; convert auth failures to a proper
        # JSON 401 response here.
        try:
            authorization = self._get_authorization(request)
            token = self._parse_bearer(authorization)
            request.state.token_claims = self._decode_token(token)
        except HTTPException as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
                headers=exc.headers,
            )
        return await call_next(request)

    def _get_authorization(self, request: Request) -> str:
        authorization = request.headers.get("Authorization")
        if authorization:
            return authorization
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing",
            headers=_www_auth_header(),
        )

    def _parse_bearer(self, authorization: str) -> str:
        try:
            scheme, token = authorization.split()
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authorization header should be in the format 'Bearer <token>'",
                headers=_www_auth_header(),
            ) from exc
        if scheme.lower() != "bearer":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication scheme must be Bearer",
                headers=_www_auth_header(),
            )
        return token

    def _decode_token(self, token: str) -> dict[str, Any]:
        try:
            return cast(
                dict[str, Any],
                jwt.decode(
                    token,
                    self._jwt_secret_key,
                    algorithms=[_ALGORITHM],
                    options={"verify_exp": True},
                ),
            )
        except jwt.PyJWTError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token",
                headers=_www_auth_header(),
            ) from exc
