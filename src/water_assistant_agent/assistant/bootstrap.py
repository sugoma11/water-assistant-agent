"""Application factory: deferred bootstrap of the FastAPI HTTP layer.

Adapted from core-agent ``bootstrap.py``. Since this is a single-tenant service
the per-tenant machinery (tenant selection/validation, Sentry, OpenTelemetry,
the FinTech business routers) is dropped; what remains is the wiring that turns
the bare ADK ``root_agent`` into an HTTP service a frontend can talk to:

* a health-check endpoint,
* correlation-ID / logging middleware plus mandatory JWT bearer auth — the
  service refuses to start without ``jwt_secret_key``/``session_db_url`` (D5),
* the admin, auth and conversations routers,
* the custom ownership-gated AG-UI endpoint (``add_agent_endpoint``, FR6)
  mounted at ``/`` in place of ``add_adk_fastapi_endpoint``.

Importing this module has no side effects; everything happens in
:func:`create_bootstrap`.
"""

import dataclasses
import math
from typing import Final

import structlog
from ag_ui_adk import ADKAgent
from asgi_correlation_id import CorrelationIdMiddleware
from fastapi import FastAPI, status
from google.adk.agents.llm_agent import Agent
from google.adk.sessions.base_session_service import BaseSessionService
from google.adk.sessions.database_session_service import DatabaseSessionService
from pydantic import BaseModel

from water_assistant_agent.assistant.agents.root_agent.agent import root_agent
from water_assistant_agent.assistant.db import (
    create_all,
    create_db_engine,
    create_session_factory,
)
from water_assistant_agent.assistant.exception_handlers import (
    register_exception_handlers,
)
from water_assistant_agent.assistant.logging_config import setup_logging
from water_assistant_agent.assistant.middlewares import (
    BearerTokenMiddleware,
    LoggingMiddleware,
)
from water_assistant_agent.assistant.routers import admin_users, auth, conversations
from water_assistant_agent.assistant.routers.agent import (
    add_agent_endpoint,
    extract_verified_user_id,
)
from water_assistant_agent.assistant.settings import AssistantSettings, get_settings

_DB_POOL_SIZE: Final[int] = 10
_DB_MAX_OVERFLOW: Final[int] = 20
_DB_POOL_TIMEOUT: Final[int] = 30
_DB_POOL_RECYCLE: Final[int] = 1800

# Conversations are durable user data (they back ``/conversations``), so no ADK
# session may ever be expired. ag-ui-adk's signature types the timeout as
# ``Optional[int]``, but its cleanup pass compares ``age > self._timeout``
# unguarded — ``None`` therefore raises ``TypeError: '>' not supported between
# instances of 'float' and 'NoneType'`` once per live session on every pass,
# logged as "Error checking session <app>:<id>". ``inf`` says "never" in a form
# the comparison accepts.
_SESSION_TIMEOUT_NEVER: Final[float] = math.inf
# With nothing to expire, the pass is pure overhead: it reloads every tracked
# session (events included) from the database each interval. Keep the task alive
# — it also untracks sessions deleted out-of-band — but run it rarely.
_SESSION_CLEANUP_INTERVAL_SECONDS: Final[int] = 24 * 60 * 60

logger = structlog.get_logger(__name__)


def _require_service_config(settings: AssistantSettings) -> None:
    """Refuse to start unless the mandatory service env vars are set (D5)."""
    missing: list[str] = []
    if not settings.jwt_secret_key:
        missing.append("AGENT_JWT_SECRET")
    if not settings.session_db_url:
        missing.append("WATER_ASSISTANT_SESSION_DB_URL")
    if missing:
        raise RuntimeError(
            "water-assistant cannot start: set the required environment "
            f"variable(s): {', '.join(missing)}"
        )


class HealthCheck(BaseModel):
    """Response model for the health-check endpoint."""

    status: str = "OK"


@dataclasses.dataclass(frozen=True, slots=True)
class AppBootstrap:
    """Holds the fully-wired FastAPI application and its root agent."""

    app: FastAPI
    root_agent: Agent


def _get_health() -> HealthCheck:
    """Perform a health check.

    Used by container orchestration to gate deployment: services depending on
    this API will not roll out unless this endpoint returns HTTP 200.
    """
    return HealthCheck(status="OK")


def _build_session_service(settings: AssistantSettings) -> BaseSessionService:
    """Return the persistent ADK session service over ``session_db_url`` (D5)."""
    return DatabaseSessionService(
        db_url=settings.session_db_url,
        pool_size=_DB_POOL_SIZE,
        max_overflow=_DB_MAX_OVERFLOW,
        pool_timeout=_DB_POOL_TIMEOUT,
        pool_recycle=_DB_POOL_RECYCLE,
    )


def _add_adk_endpoint(
    app: FastAPI,
    settings: AssistantSettings,
    agent: Agent,
    session_service: BaseSessionService,
) -> None:
    """Mount the custom AG-UI endpoint using a verified-identity extractor (FR6)."""
    adk_agent = ADKAgent(
        adk_agent=agent,
        app_name=settings.app_name,
        user_id_extractor=extract_verified_user_id,
        session_service=session_service,
        session_timeout_seconds=_SESSION_TIMEOUT_NEVER,  # never expire sessions
        cleanup_interval_seconds=_SESSION_CLEANUP_INTERVAL_SECONDS,
        # Belt and braces on the two lines above: cleanup is the only path in
        # ag-ui-adk that deletes sessions, and it deletes by default. User-driven
        # deletion goes straight to the session service (``routers.conversations``)
        # and is unaffected.
        delete_session_on_cleanup=False,
        use_in_memory_services=True,  # in-memory artifact/memory; DB session above
        # D3/FR11: the conversation id *is* the ADK session id. Without this flag
        # ag-ui-adk defaults to generating its own session id and only records the
        # thread id in session state (``_ag_ui_thread_id``) — which silently breaks
        # every route that looks a session up by conversation id (history restore,
        # partial append, session delete). Safe here because DatabaseSessionService
        # honours a caller-supplied session_id (the default exists for backends like
        # VertexAI that mint their own).
        use_thread_id_as_session_id=True,
    )
    add_agent_endpoint(app, adk_agent, path="/")


def create_bootstrap(
    settings_override: AssistantSettings | None = None,
) -> AppBootstrap:
    """Build and return the fully configured app together with the root agent.

    Args:
        settings_override: Optional settings object. When *None* the process-wide
            settings singleton is used and logging is configured as a side
            effect. Pass an :class:`AssistantSettings` instance (e.g. from tests)
            to skip the global logging setup.

    Returns:
        An :class:`AppBootstrap` with the wired :class:`~fastapi.FastAPI`
        application and the root :class:`~google.adk.agents.llm_agent.Agent`.
    """
    if settings_override is None:
        settings = get_settings()
        setup_logging()
        logger.info("Starting water-assistant service", log_context="startup")
    else:
        settings = settings_override

    _require_service_config(settings)

    # One database (session_db_url) backs the app tables *and* ADK sessions (A1).
    engine = create_db_engine(settings.session_db_url)
    create_all(engine)  # app_users + conversations (OQ3; ADK builds its own)
    session_service = _build_session_service(settings)

    app = FastAPI(title="Water Assistant Agent")
    # Shared state read by routers/dependencies at request time.
    app.state.settings = settings
    app.state.db_sessionmaker = create_session_factory(engine)
    app.state.session_service = session_service

    register_exception_handlers(app)
    app.add_api_route(
        "/health",
        _get_health,
        tags=["healthcheck"],
        summary="Perform a Health Check",
        response_description="Return HTTP Status Code 200 (OK)",
        status_code=status.HTTP_200_OK,
        response_model=HealthCheck,
    )
    app.include_router(admin_users.router)
    app.include_router(auth.router)
    app.include_router(conversations.router)

    _add_adk_endpoint(app, settings, root_agent, session_service)

    # Starlette runs middleware in reverse registration order, so the first
    # add_middleware call is outermost (runs first on every request).
    # outermost — generates the request ID first
    app.add_middleware(CorrelationIdMiddleware)
    # mandatory bearer auth — config validation above guarantees a secret (D5)
    app.add_middleware(
        BearerTokenMiddleware,
        jwt_secret_key=settings.jwt_secret_key,
    )
    # innermost — runs closest to the handler, after the correlation ID is set
    app.add_middleware(LoggingMiddleware)

    return AppBootstrap(app=app, root_agent=root_agent)


def create_app(settings_override: AssistantSettings | None = None) -> FastAPI:
    """Return just the FastAPI instance; thin wrapper over :func:`create_bootstrap`."""
    return create_bootstrap(settings_override).app
