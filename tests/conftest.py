"""Shared fixtures for the assistant API tests (Phase 1+)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from water_assistant_agent.assistant.bootstrap import create_bootstrap
from water_assistant_agent.assistant.settings import AssistantSettings

JWT_SECRET = "test-secret-key-please-change-0123456789"
ADMIN_KEY = "test-admin-key"


def build_client(tmp_path: Path, *, admin_api_key: str | None) -> TestClient:
    """Build a TestClient over a throwaway SQLite database."""
    db = tmp_path / "test.db"
    settings = AssistantSettings(
        _env_file=None,  # ignore the project .env for deterministic tests
        jwt_secret_key=JWT_SECRET,
        session_db_url=f"sqlite+aiosqlite:///{db}",
        admin_api_key=admin_api_key,
        auth_token_ttl_days=7,
    )
    return TestClient(create_bootstrap(settings).app)


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """A configured service with the admin API key set."""
    with build_client(tmp_path, admin_api_key=ADMIN_KEY) as test_client:
        yield test_client


@pytest.fixture
def client_no_admin(tmp_path: Path) -> Iterator[TestClient]:
    """A service with no admin key configured (admin API fails closed, EC6)."""
    with build_client(tmp_path, admin_api_key=None) as test_client:
        yield test_client


@pytest.fixture
def admin_headers() -> dict[str, str]:
    return {"X-Admin-API-Key": ADMIN_KEY}


@pytest.fixture
def admin_key() -> str:
    return ADMIN_KEY


@pytest.fixture
def jwt_secret() -> str:
    return JWT_SECRET
