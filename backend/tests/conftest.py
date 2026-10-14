"""Shared fixtures: an in-process TestClient over a fresh in-memory database."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

# Seeded demo credentials (see app/store.py).
DBA = ("dba", "dba-password")

# Each test gets its own isolated, empty SQLite database.
TEST_DATABASE_URL = "sqlite:///:memory:"


@pytest.fixture()
def client() -> TestClient:
    app = create_app(database_url=TEST_DATABASE_URL)
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def auth_headers(client: TestClient) -> dict[str, str]:
    """Log in as the seeded DBA and return Authorization headers."""
    response = client.post("/auth/login", json={"username": DBA[0], "password": DBA[1]})
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
