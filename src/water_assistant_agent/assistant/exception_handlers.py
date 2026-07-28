"""Shared FastAPI exception handler registration.

Ported from core-agent ``api/exception_handlers.py``, trimmed to the single
:class:`ServiceError` mapping this project uses.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from water_assistant_agent.assistant.exceptions import ServiceError


def _service_exception_handler(request: Request, exc: ServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register shared exception handlers on the given FastAPI application."""
    app.add_exception_handler(
        ServiceError,
        _service_exception_handler,  # type: ignore[arg-type]
    )
