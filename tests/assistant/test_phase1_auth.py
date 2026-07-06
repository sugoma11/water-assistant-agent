"""Phase 1 API tests: admin CRUD, key auth, login, token expiry (T008).

Covers FR2, EC4, EC6, SC1 (admin key + fail-closed + duplicate email),
FR4/EC1 (login + expiry), and SC6 (no registration route).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient


def _create_user(
    client: TestClient,
    admin_headers: dict[str, str],
    *,
    email: str = "alice@example.com",
    password: str = "s3cret-pw",
    display_name: str | None = None,
) -> dict:
    resp = client.post(
        "/admin/users",
        headers=admin_headers,
        json={"email": email, "password": password, "display_name": display_name},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- Admin CRUD + response hygiene ------------------------------------------


def test_admin_crud_roundtrip(client: TestClient, admin_headers: dict[str, str]) -> None:
    created = _create_user(client, admin_headers, display_name="Alice")
    assert created["email"] == "alice@example.com"
    assert created["display_name"] == "Alice"
    assert "password_hash" not in created
    assert "password" not in created
    user_id = created["id"]

    listed = client.get("/admin/users", headers=admin_headers).json()
    assert [u["id"] for u in listed] == [user_id]
    assert all("password_hash" not in u for u in listed)

    patched = client.patch(
        f"/admin/users/{user_id}",
        headers=admin_headers,
        json={"display_name": "Alice B"},
    )
    assert patched.status_code == 200
    assert patched.json()["display_name"] == "Alice B"

    deleted = client.delete(f"/admin/users/{user_id}", headers=admin_headers)
    assert deleted.status_code == 204
    assert client.get("/admin/users", headers=admin_headers).json() == []


def test_email_is_normalized(client: TestClient, admin_headers: dict[str, str]) -> None:
    created = _create_user(client, admin_headers, email="  MixedCase@Example.COM ")
    assert created["email"] == "mixedcase@example.com"


# --- Admin key auth (FR2, EC6, SC1) -----------------------------------------


@pytest.mark.parametrize("headers", [{}, {"X-Admin-API-Key": "wrong"}])
def test_admin_requires_valid_key(
    client: TestClient, headers: dict[str, str], admin_key: str
) -> None:
    resp = client.get("/admin/users", headers=headers)
    assert resp.status_code == 401
    assert admin_key not in resp.text  # the key is never echoed


def test_admin_fails_closed_when_unconfigured(
    client_no_admin: TestClient, admin_key: str
) -> None:
    # Even with the "right" key, an unconfigured admin API returns 503.
    resp = client_no_admin.get("/admin/users", headers={"X-Admin-API-Key": admin_key})
    assert resp.status_code == 503


# --- Duplicate email (EC4) ---------------------------------------------------


def test_duplicate_email_conflict(client: TestClient, admin_headers: dict[str, str]) -> None:
    _create_user(client, admin_headers, email="dup@example.com")
    resp = client.post(
        "/admin/users",
        headers=admin_headers,
        json={"email": "DUP@example.com", "password": "x"},  # case-insensitive dup
    )
    assert resp.status_code == 409


# --- Login (FR4) + uniform failure -------------------------------------------


def test_login_success_and_me(client: TestClient, admin_headers: dict[str, str]) -> None:
    _create_user(client, admin_headers, email="bob@example.com", password="pw12345")
    resp = client.post(
        "/auth/login", json={"email": "bob@example.com", "password": "pw12345"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"] and body["expires_at"]

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["email"] == "bob@example.com"


def test_login_failures_are_uniform_401(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    _create_user(client, admin_headers, email="carol@example.com", password="rightpw")
    bad_pw = client.post(
        "/auth/login", json={"email": "carol@example.com", "password": "wrongpw"}
    )
    unknown = client.post(
        "/auth/login", json={"email": "nobody@example.com", "password": "whatever"}
    )
    assert bad_pw.status_code == unknown.status_code == 401
    # Same message for both, so bad password and unknown email are indistinguishable.
    assert bad_pw.json()["detail"] == unknown.json()["detail"]


def test_expired_token_rejected(client: TestClient, jwt_secret: str) -> None:
    expired = jwt.encode(
        {
            "sub": "any-user",
            "email": "x@example.com",
            "exp": int((datetime.now(UTC) - timedelta(days=1)).timestamp()),
        },
        jwt_secret,
        algorithm="HS256",
    )
    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert resp.status_code == 401


# --- No registration path (SC6) ---------------------------------------------


def test_no_registration_route_exists(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert not any(
        keyword in path.lower()
        for path in paths
        for keyword in ("register", "signup", "sign-up")
    )
