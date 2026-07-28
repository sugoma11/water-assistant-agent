"""Structured logging setup for the assistant service.

A slimmed-down port of core-agent ``infra/logger.py``: same structlog console
rendering and LiteLLM/uvicorn noise suppression, but without the Sentry /
OpenTelemetry integrations (not dependencies of this project).
"""

import logging
import warnings
from typing import Final

import litellm
import structlog

_UVICORN_LOGGERS: Final[tuple[str, ...]] = ("uvicorn", "uvicorn.error")


def setup_logging(level: str = "INFO") -> None:
    """Configure structlog + stdlib logging with a console renderer."""
    warnings.filterwarnings("ignore")
    # Suppress noisy LiteLLM stdout hints across the service.
    litellm.set_verbose = False  # type: ignore[attr-defined]
    litellm.suppress_debug_info = True
    litellm.turn_off_message_logging = True

    log_level = logging.getLevelNamesMapping()[level.upper()]

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    logging.getLogger().setLevel(log_level)
    # Let uvicorn's own access logging through structlog's contextvars instead.
    for logger_name in _UVICORN_LOGGERS:
        logging.getLogger(logger_name).setLevel(log_level)
    logging.getLogger("uvicorn.access").propagate = False
