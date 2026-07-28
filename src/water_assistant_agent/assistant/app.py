"""ASGI application entry-point.

Delegates all construction to :func:`~water_assistant_agent.assistant.bootstrap.create_bootstrap`
so that importing this module has no side effects beyond running the factory.
The module-level ``app`` object is what Uvicorn discovers at startup; ``main``
backs the ``water-assistant`` console script and serves it directly.
"""

import uvicorn

from water_assistant_agent.assistant.bootstrap import create_bootstrap
from water_assistant_agent.assistant.settings import get_settings

_bootstrap = create_bootstrap()
app = _bootstrap.app
root_agent = _bootstrap.root_agent


def main() -> None:
    """Serve the assistant with Uvicorn (entry point for ``water-assistant``)."""
    settings = get_settings()
    uvicorn.run(
        "water_assistant_agent.assistant.app:app",
        host=settings.host,
        port=settings.port,
    )


if __name__ == "__main__":
    main()
