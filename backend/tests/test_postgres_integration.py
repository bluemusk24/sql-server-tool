"""Live Postgres round-trip — env-gated, like the live SQL Server test.

Skipped unless ``SDIP_TEST_DATABASE_URL`` points at a **disposable**
Postgres database (point it at the `make postgres` container):

    make postgres
    SDIP_TEST_DATABASE_URL=postgresql://sdip:sdip@localhost:5432/sdip \
        uv run pytest tests/test_postgres_integration.py

The app bootstraps the schema and seeds demo data on startup, so the
tests below exercise the full path a real deployment takes against
Postgres: create_all, Alembic stamp, seed, then API reads and writes.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

DATABASE_URL = os.getenv("SDIP_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="SDIP_TEST_DATABASE_URL not set (no Postgres to test against)",
)


@pytest.fixture()
def client() -> TestClient:
    # Imported lazily so the skip check above never drags the app in.
    from app.main import create_app

    app = create_app(database_url=DATABASE_URL)
    with TestClient(app) as test_client:
        yield test_client


def _login(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/auth/login",
        json={"username": "dba", "password": "dba-password"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_health_and_seed_on_postgres(client: TestClient) -> None:
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    # The seeded demo user must exist on a fresh database.
    assert _login(client)["Authorization"].startswith("Bearer ")


def test_investigation_round_trip_on_postgres(client: TestClient) -> None:
    headers = _login(client)

    created = client.post(
        "/investigations",
        headers=headers,
        json={
            "server_host": "sql-pg-test.example",
            "server_port": 1433,
            "auth_type": "sql",
            "databases": None,
            "lookback_hours": 4,
        },
    )
    assert created.status_code == 201, created.text

    listed = client.get("/investigations", headers=headers)
    assert listed.status_code == 200
    hosts = {entry["server_host"] for entry in listed.json()}
    assert "sql-pg-test.example" in hosts
