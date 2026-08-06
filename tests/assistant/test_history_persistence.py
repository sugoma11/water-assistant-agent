"""Regression: a finished turn is readable back as history (D3, FR9, FR11, US6).

This is the invariant the whole restore path rests on — "a conversation ``id``
*is* the AG-UI thread id and ADK session id" — and it was silently false.
``ADKAgent``'s ``use_thread_id_as_session_id`` defaults to *False*, in which mode
ag-ui-adk mints its own ``sessions.id`` and only records the thread id in session
state. Every lookup keyed by conversation id therefore missed: ``GET
/conversations/{id}/messages`` answered ``{"messages": []}``, so a browser reload
showed an empty pane even though the turns were sitting in the database.

The tests below run a real turn through the agent endpoint against a stubbed LLM
(no network) and then read the history back through the REST route the frontend
uses, which is exactly the path that was broken.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from sqlalchemy import text

from water_assistant_agent.assistant.db import create_db_engine

ANSWER = "Rainfall totalled 42 mm."


def _provision(
    client: TestClient, admin_headers: dict[str, str], *, email: str
) -> dict[str, str]:
    """Create a user and log in; return the bearer headers."""
    created = client.post(
        "/admin/users",
        headers=admin_headers,
        json={"email": email, "password": "pw-12345678"},
    )
    assert created.status_code == 201, created.text
    login = client.post("/auth/login", json={"email": email, "password": "pw-12345678"})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _run_payload(thread_id: str, text: str) -> dict[str, Any]:
    """An AG-UI RunAgentInput carrying one user message."""
    return {
        "threadId": thread_id,
        "runId": str(uuid.uuid4()),
        "state": {},
        "messages": [{"id": str(uuid.uuid4()), "role": "user", "content": text}],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }


@pytest.fixture
def stub_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Answer every model call with a fixed text reply — keeps the test offline."""

    async def fake_generate(  # noqa: ANN401 - mirrors the ADK signature loosely
        self: LiteLlm, llm_request: Any, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=ANSWER)])
        )

    monkeypatch.setattr(LiteLlm, "generate_content_async", fake_generate)


def _texts(messages: list[dict[str, Any]], role: str) -> list[str]:
    """Content strings of the messages with *role*."""
    return [
        m["content"]
        for m in messages
        if m.get("role") == role and isinstance(m.get("content"), str)
    ]


def test_finished_turn_is_restored_from_history(
    client: TestClient, admin_headers: dict[str, str], stub_llm: None
) -> None:
    """The turn a user just had comes back from GET /messages (the reload path)."""
    alice = _provision(client, admin_headers, email="alice@example.com")
    conv_id = client.post("/conversations", headers=alice, json={}).json()["id"]

    run = client.post(
        "/", headers=alice, json=_run_payload(conv_id, "How much rain fell?")
    )
    assert run.status_code == 200, run.text
    assert "RUN_ERROR" not in run.text

    messages = client.get(f"/conversations/{conv_id}/messages", headers=alice).json()[
        "messages"
    ]
    assert messages, "history came back empty — the reload would show a blank pane"
    assert "How much rain fell?" in _texts(messages, "user")
    assert any(ANSWER in text for text in _texts(messages, "assistant"))


def test_adk_session_is_keyed_by_conversation_id(
    client: TestClient, admin_headers: dict[str, str], stub_llm: None
) -> None:
    """D3/FR11: the run stores its session under the conversation id, not a fresh one.

    Asserted against the stored row rather than through ``/messages`` because this
    identity is what ``/messages``, ``/partial`` *and* the ADK-session half of
    DELETE all rely on; when it broke, all three failed together and orphan
    sessions accumulated on every delete.
    """
    alice = _provision(client, admin_headers, email="alice@example.com")
    conv_id = client.post("/conversations", headers=alice, json={}).json()["id"]

    assert (
        client.post("/", headers=alice, json=_run_payload(conv_id, "hi")).status_code
        == 200
    )

    # Read ADK's own table with a sync engine over the same database (A1).
    engine = create_db_engine(client.app.state.settings.session_db_url)  # type: ignore[attr-defined]
    with engine.connect() as conn:
        session_ids = [row[0] for row in conn.execute(text("SELECT id FROM sessions"))]
    assert session_ids == [conv_id], (
        "the ADK session must be keyed by the conversation id; a generated id "
        "strands the history behind every conversation-id lookup"
    )


def test_partial_append_survives_reload(
    client: TestClient, admin_headers: dict[str, str], stub_llm: None
) -> None:
    """A stopped answer persisted via /partial is readable back (C7, R2)."""
    alice = _provision(client, admin_headers, email="alice@example.com")
    conv_id = client.post("/conversations", headers=alice, json={}).json()["id"]
    # /partial needs an existing ADK session, so have a turn first.
    assert (
        client.post("/", headers=alice, json=_run_payload(conv_id, "hi")).status_code
        == 200
    )

    stopped = client.post(
        f"/conversations/{conv_id}/partial",
        headers=alice,
        json={"content": "Partial answer that was cut off"},
    )
    assert stopped.status_code == 201, stopped.text

    messages = client.get(f"/conversations/{conv_id}/messages", headers=alice).json()[
        "messages"
    ]
    assert any(
        "Partial answer that was cut off" in text
        for text in _texts(messages, "assistant")
    )
