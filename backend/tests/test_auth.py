"""Tests for the health and auth endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_health_is_unauthenticated(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"]


def test_health_reports_the_serving_commit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RENDER_GIT_COMMIT", "abc1234def")
    body = client.get("/health").json()
    assert body["commit"] == "abc1234def"
    # The original fields still hold for existing consumers (#38).
    assert body["status"] == "ok"
    assert body["version"]


def test_health_commit_is_null_when_the_runtime_supplies_none(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    body = client.get("/health").json()
    # The key is always present: CI distinguishes "old build without the
    # field" (key missing → 200-only fallback) from "field unknown"
    # (null → fallback) from "field set" (must match the pushed commit).
    assert "commit" in body
    assert body["commit"] is None


def test_login_returns_bearer_token(client: TestClient) -> None:
    response = client.post(
        "/auth/login", json={"username": "dba", "password": "dba-password"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]


def test_login_rejects_bad_password(client: TestClient) -> None:
    response = client.post(
        "/auth/login", json={"username": "dba", "password": "wrong"}
    )
    assert response.status_code == 401


def test_login_rejects_unknown_user(client: TestClient) -> None:
    response = client.post(
        "/auth/login", json={"username": "nobody", "password": "x"}
    )
    assert response.status_code == 401


def test_session_returns_actor(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/auth/session", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["organization"] == "Acme Corp"
    assert body["actor"]


def test_session_401_without_token(client: TestClient) -> None:
    assert client.get("/auth/session").status_code == 401


def test_session_401_with_bad_token(client: TestClient) -> None:
    response = client.get(
        "/auth/session", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert response.status_code == 401


def test_authorize_returns_url(client: TestClient) -> None:
    response = client.get("/auth/authorize")
    assert response.status_code == 200
    assert response.json()["authorize_url"]
