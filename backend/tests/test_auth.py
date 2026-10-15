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
    monkeypatch.delenv("SDIP_BUILD_SHA", raising=False)
    body = client.get("/health").json()
    # The key is always present: CI distinguishes "old build without the
    # field" (key missing → 200-only fallback) from "field unknown"
    # (null → fallback) from "field set" (must match the pushed commit).
    assert "commit" in body
    assert body["commit"] is None


def test_health_prefers_the_baked_image_sha_over_the_runtime_env(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Registry deploys (#42): the image's baked identity is what is
    # actually running, even if the runtime injects a newer SHA (the
    # repo tip may have moved past the image being promoted).
    monkeypatch.setenv("SDIP_BUILD_SHA", "baked456")
    monkeypatch.setenv("RENDER_GIT_COMMIT", "runtime789")
    body = client.get("/health").json()
    assert body["commit"] == "baked456"


def test_health_reports_the_registry_image_reference(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "SDIP_IMAGE", "ghcr.io/bluemusk24/sql-server-tool:20260818-163457-83242da"
    )
    body = client.get("/health").json()
    assert body["image"] == "ghcr.io/bluemusk24/sql-server-tool:20260818-163457-83242da"


def test_health_image_is_null_for_non_registry_builds(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SDIP_IMAGE", raising=False)
    body = client.get("/health").json()
    # Key always present (same reasoning as commit, #42).
    assert "image" in body
    assert body["image"] is None


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


def test_authorize_is_unavailable_when_sso_is_not_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OIDC_ISSUER", raising=False)
    monkeypatch.delenv("OIDC_CLIENT_ID", raising=False)
    response = client.get("/auth/authorize")
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]
