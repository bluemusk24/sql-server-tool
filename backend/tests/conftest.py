"""Shared fixtures: an in-process TestClient over a fresh store."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

# Seeded demo credentials (see app/store.py).
DBA = ("dba", "dba-password")


@pytest.fixture()
def client() -> TestClient:
    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def auth_headers(client: TestClient) -> dict[str, str]:
    """Log in as the seeded DBA and return Authorization headers."""
    response = client.post("/auth/login", json={"username": DBA[0], "password": DBA[1]})
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
