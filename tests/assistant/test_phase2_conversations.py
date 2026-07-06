"""Phase 2 API tests: conversation isolation, auth gating, identity injection (T013).

Covers the two-user isolation matrix with *valid* credentials (FR13, NFR3, SC2,
EC3), unauthenticated rejection of every chat/conversation route (FR5, SC6),
server-side identity injection overriding client-supplied props (FR6),
deleted-user revocation (EC5), and delete-then-repost (no stale-cache
resurrection, R5, EC8).
"""

from __future__ import annotations

import uuid

import pytest
from ag_ui_adk import ADKAgent
from fastapi.testclient import TestClient

from water_assistant_agent.assistant.routers.agent import extract_verified_user_id


def _provision(
    client: TestClient,
    admin_headers: dict[str, str],
    *,
    email: str,
    password: str = "pw-12345678",
) -> tuple[dict[str, str], str]:
    """Create a user and log in; return ``(bearer_headers, user_id)``."""
    created = client.post(
        "/admin/users", headers=admin_headers, json={"email": email, "password": password}
    )
    assert created.status_code == 201, created.text
    user_id = created.json()["id"]
    login = client.post("/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    return headers, user_id


def _run_payload(thread_id: str, *, forwarded_props: dict | None = None) -> dict:
    """A minimal valid AG-UI RunAgentInput body."""
    return {
        "threadId": thread_id,
        "runId": str(uuid.uuid4()),
        "state": {},
        "messages": [],
        "tools": [],
        "context": [],
        "forwardedProps": forwarded_props or {},
    }


# --- CRUD + title derivation -------------------------------------------------


def test_create_list_rename_delete(client: TestClient, admin_headers: dict[str, str]) -> None:
    alice, _ = _provision(client, admin_headers, email="alice@example.com")

    created = client.post(
        "/conversations", headers=alice, json={"first_message": "How much rain fell?"}
    )
    assert created.status_code == 201, created.text
    conv = created.json()
    assert conv["title"] == "How much rain fell?"
    conv_id = conv["id"]

    listed = client.get("/conversations", headers=alice).json()
    assert [c["id"] for c in listed] == [conv_id]

    renamed = client.patch(
        f"/conversations/{conv_id}", headers=alice, json={"title": "Rainfall"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Rainfall"

    deleted = client.delete(f"/conversations/{conv_id}", headers=alice)
    assert deleted.status_code == 204
    assert client.get("/conversations", headers=alice).json() == []


def test_title_defaults_and_truncates(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    alice, _ = _provision(client, admin_headers, email="alice@example.com")

    empty = client.post("/conversations", headers=alice, json={}).json()
    assert empty["title"] == "New conversation"

    long_msg = "x" * 200
    truncated = client.post(
        "/conversations", headers=alice, json={"first_message": long_msg}
    ).json()
    assert len(truncated["title"]) < len(long_msg)
    assert truncated["title"].endswith("…")


# --- Two-user isolation matrix (FR13, NFR3, SC2, EC3) ------------------------


def test_two_user_isolation(client: TestClient, admin_headers: dict[str, str]) -> None:
    alice, _ = _provision(client, admin_headers, email="alice@example.com")
    bob, _ = _provision(client, admin_headers, email="bob@example.com")

    conv_id = client.post("/conversations", headers=alice, json={}).json()["id"]

    # Bob never sees Alice's conversation in his sidebar list.
    assert client.get("/conversations", headers=bob).json() == []

    # Every cross-user access to Alice's conversation is a uniform 404 for Bob.
    assert client.get(f"/conversations/{conv_id}/messages", headers=bob).status_code == 404
    assert (
        client.patch(
            f"/conversations/{conv_id}", headers=bob, json={"title": "hijack"}
        ).status_code
        == 404
    )
    assert client.delete(f"/conversations/{conv_id}", headers=bob).status_code == 404
    # Foreign thread_id on the agent endpoint gets the same 404 (EC3).
    assert client.post("/", headers=bob, json=_run_payload(conv_id)).status_code == 404
    # Unknown id is indistinguishable from a foreign one.
    assert (
        client.post("/", headers=bob, json=_run_payload("no-such-thread")).status_code == 404
    )

    # Alice's conversation is untouched by Bob's attempts.
    assert [c["id"] for c in client.get("/conversations", headers=alice).json()] == [conv_id]


# --- Unauthenticated rejection of every route (FR5, SC6) ---------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/conversations"),
        ("post", "/conversations"),
        ("patch", "/conversations/x"),
        ("delete", "/conversations/x"),
        ("get", "/conversations/x/messages"),
        ("post", "/conversations/x/partial"),
        ("post", "/"),
    ],
)
def test_routes_require_auth(client: TestClient, method: str, path: str) -> None:
    resp = client.request(method, path, json={})
    assert resp.status_code == 401


# --- Identity injection (FR6) ------------------------------------------------


def test_client_supplied_identity_is_overridden(
    client: TestClient, admin_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    alice, alice_id = _provision(client, admin_headers, email="alice@example.com")
    conv_id = client.post("/conversations", headers=alice, json={}).json()["id"]

    captured: dict[str, str] = {}

    async def stub_run(self: ADKAgent, run_input):  # noqa: ANN001, ANN202
        captured["user_id"] = extract_verified_user_id(run_input)
        return
        yield  # make this an (empty) async generator

    monkeypatch.setattr(ADKAgent, "run", stub_run)

    resp = client.post(
        "/",
        headers=alice,
        json=_run_payload(conv_id, forwarded_props={"user_id": "SPOOFED"}),
    )
    assert resp.status_code == 200
    # The server-verified id wins; the client-supplied "SPOOFED" is discarded.
    assert captured["user_id"] == alice_id


# --- Deleted-user revocation (EC5) -------------------------------------------


def test_deleted_user_token_revoked(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    alice, alice_id = _provision(client, admin_headers, email="alice@example.com")
    conv_id = client.post("/conversations", headers=alice, json={}).json()["id"]

    # Delete the account out from under a still-valid token.
    assert client.delete(f"/admin/users/{alice_id}", headers=admin_headers).status_code == 204

    assert client.get("/conversations", headers=alice).status_code == 401
    assert client.post("/", headers=alice, json=_run_payload(conv_id)).status_code == 401


# --- Delete-then-repost: no stale-cache resurrection (R5, EC8) ----------------


def test_delete_then_repost_no_resurrection(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    alice, _ = _provision(client, admin_headers, email="alice@example.com")
    conv_id = client.post("/conversations", headers=alice, json={}).json()["id"]

    assert client.delete(f"/conversations/{conv_id}", headers=alice).status_code == 204

    # A fresh conversation gets a new id; the deleted one cannot be reused.
    new_id = client.post("/conversations", headers=alice, json={}).json()["id"]
    assert new_id != conv_id

    # The old thread id no longer names an owned conversation — uniform 404,
    # so a cached ADK session cannot resurrect access to it.
    assert client.post("/", headers=alice, json=_run_payload(conv_id)).status_code == 404
    assert client.get(f"/conversations/{conv_id}/messages", headers=alice).status_code == 404
