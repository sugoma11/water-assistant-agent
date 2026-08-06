"""Regression: the ADK session-cleanup pass must be quiet and harmless (D5, FR11).

Conversations are durable user data — ``/conversations`` reads them back — so
``bootstrap`` wires ``ADKAgent`` to never expire a session. That used to be
spelled ``session_timeout_seconds=None``, which ag-ui-adk's signature accepts
(``Optional[int]``) but its cleanup pass does not: it compares ``age >
self._timeout`` unguarded, so every pass raised ``TypeError: '>' not supported
between instances of 'float' and 'NoneType'`` once per live session and swallowed
it into an error log the backend emitted every five minutes::

    Error checking session water_assistant:f8b92989-f620-4f02-be7d-2db0b80fe6f8

Sessions did survive — but only by accident, because the exception aborted the
pass just before the delete. The test drives the *production* wiring (the real
``SessionManager`` ``create_bootstrap`` builds, over the real
``DatabaseSessionService``) with the clock ten years ahead, and pins both halves:
the pass logs nothing, and the session is still there afterwards.
"""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
from ag_ui_adk import ADKAgent
from ag_ui_adk import session_manager as session_manager_module

from water_assistant_agent.assistant import bootstrap
from water_assistant_agent.assistant.settings import AssistantSettings

APP_NAME = "water_assistant"
USER_ID = "user-1"
TEN_YEARS_SECONDS = 10 * 365 * 24 * 60 * 60


def _build_session_manager(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Any:  # ag_ui_adk.SessionManager
    """Boot the service and hand back the SessionManager it wired up."""
    built: list[ADKAgent] = []

    class _RecordingADKAgent(ADKAgent):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            built.append(self)

    monkeypatch.setattr(bootstrap, "ADKAgent", _RecordingADKAgent)
    bootstrap.create_bootstrap(
        AssistantSettings(
            _env_file=None,  # ignore the project .env for deterministic tests
            jwt_secret_key="test-secret-key-please-change-0123456789",
            session_db_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
            admin_api_key="test-admin-key",
        )
    )
    assert len(built) == 1
    return built[0]._session_manager


@pytest.mark.asyncio
async def test_cleanup_pass_keeps_sessions_and_logs_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    manager = _build_session_manager(tmp_path, monkeypatch)
    try:
        thread_id = str(uuid.uuid4())
        _, session_id = await manager.get_or_create_session(
            thread_id=thread_id, app_name=APP_NAME, user_id=USER_ID
        )

        # Run the pass as if the session had been idle for a decade. Swapping the
        # module's ``time`` reference (the pass only ever calls ``time.time()``)
        # keeps the shifted clock out of every other module.
        ten_years_on = time.time() + TEN_YEARS_SECONDS

        class _Clock:
            time = staticmethod(lambda: ten_years_on)

        monkeypatch.setattr(session_manager_module, "time", _Clock)

        with caplog.at_level(logging.WARNING, logger=session_manager_module.__name__):
            await manager._cleanup_expired_sessions()

        assert [r.getMessage() for r in caplog.records] == []

        survivor = await manager._session_service.get_session(
            app_name=APP_NAME, user_id=USER_ID, session_id=session_id
        )
        assert survivor is not None
    finally:
        await manager.stop_cleanup_task()
