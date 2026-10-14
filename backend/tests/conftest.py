"""Shared fixtures: an in-process TestClient over a fresh in-memory database."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

# Seeded demo credentials (see app/store.py).
DBA = ("dba", "dba-password")

# Each test gets its own isolated, empty SQLite database.
TEST_DATABASE_URL = "sqlite:///:memory:"


@pytest.fixture(autouse=True)
def _track_logger_disabled(request: pytest.FixtureRequest) -> None:
    """Report any test that flips `.disabled` on the app loggers (CI-only bug hunt).

    Something in the suite was disabling the sqldiag loggers on GitHub
    Actions, silencing test_backend_log_names_target_server. Print the
    transition so the CI log names the culprit test.
    """
    watch = ["sqldiag", "sqldiag.investigations"]
    before = {name: logging.getLogger(name).disabled for name in watch}
    yield
    after = {name: logging.getLogger(name).disabled for name in watch}
    if before != after:
        print(f"LOGGER-DISABLED-CHANGE in {request.node.name}: {before} -> {after}")


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
