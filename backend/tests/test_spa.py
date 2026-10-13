"""The API serves the built frontend when $FRONTEND_DIST names it (#35)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

TEST_DATABASE_URL = "sqlite:///:memory:"


@pytest.fixture()
def dist(tmp_path: Path) -> Path:
    """A miniature of the Vite output: index.html + hashed asset + icon."""
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text(
        "<!doctype html><html><body><div id=root></div></body></html>",
        encoding="utf-8",
    )
    (tmp_path / "assets" / "app-abc123.js").write_text(
        "console.log('boot')", encoding="utf-8"
    )
    (tmp_path / "favicon.ico").write_bytes(b"\x00\x01")
    return tmp_path


@pytest.fixture()
def spa_client(dist: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("FRONTEND_DIST", str(dist))
    app = create_app(database_url=TEST_DATABASE_URL)
    with TestClient(app) as client:
        yield client


def test_serves_index_html_at_the_root_and_for_spa_routes(
    spa_client: TestClient,
) -> None:
    # Browsers send Accept: text/html on page loads — the same request a
    # user makes when deep-linking or refreshing.
    document = {"Accept": "text/html"}
    spa_routes = [
        "/",
        "/login",
        "/investigations/new",  # collides with the API's /{inv_id} route
        "/investigations/7/approve",
        "/investigations/7/report",
        "/deeply/unknown/client/route",
    ]
    for path in spa_routes:
        response = spa_client.get(path, headers=document)
        assert response.status_code == 200, path
        assert "text/html" in response.headers["content-type"], path
        assert "<div id=root>" in response.text, path


def test_document_navigations_to_api_colliding_paths_get_the_spa(
    spa_client: TestClient,
) -> None:
    # A page load has no bearer token (it lives in localStorage), so
    # without this fallback the API's auth guard would answer 401 and
    # the browser would show JSON instead of the app (#35).
    document = {"Accept": "text/html"}
    for path in ("/investigations", "/investigations/new", "/jobs/job-123"):
        response = spa_client.get(path, headers=document)
        assert response.status_code == 200, path
        assert "<div id=root>" in response.text, path


def test_api_calls_to_colliding_paths_keep_their_api_behavior(
    spa_client: TestClient,
) -> None:
    # Same paths, API-style request (httpx default Accept: */*, no
    # text/html): the auth guard still answers 401 JSON — the fallback
    # never masks the API from actual API clients.
    for path in ("/investigations", "/investigations/new", "/jobs/job-123"):
        response = spa_client.get(path)
        assert response.status_code == 401, path
        assert "application/json" in response.headers["content-type"], path


def test_docs_survive_even_for_browser_navigations(spa_client: TestClient) -> None:
    response = spa_client.get("/docs", headers={"Accept": "text/html"})
    assert response.status_code == 200
    assert "openapi.json" in response.text


def test_serves_real_assets_and_keeps_honest_404s_for_files(
    spa_client: TestClient,
) -> None:
    asset = spa_client.get("/assets/app-abc123.js")
    assert asset.status_code == 200
    assert "console.log('boot')" in asset.text

    assert spa_client.get("/favicon.ico").status_code == 200

    # Missing files (paths with an extension) stay 404 — no HTML disguise.
    assert spa_client.get("/assets/missing-xyz.js").status_code == 404
    assert spa_client.get("/logo.png").status_code == 404


def test_api_routes_and_docs_keep_priority(spa_client: TestClient) -> None:
    health = spa_client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    # /investigations is both an SPA route and the API list endpoint:
    # the API wins by route order — its bearer-token guard answers with
    # 401 JSON (the SPA list actually lives at "/"), not HTML.
    listing = spa_client.get("/investigations")
    assert listing.status_code == 401
    assert "application/json" in listing.headers["content-type"]

    assert spa_client.get("/docs").status_code == 200
    assert spa_client.get("/openapi.json").status_code == 200


def test_api_only_app_when_env_is_unset_or_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for value in (None, "/nonexistent/dist"):
        if value is None:
            monkeypatch.delenv("FRONTEND_DIST", raising=False)
        else:
            monkeypatch.setenv("FRONTEND_DIST", value)
        app = create_app(database_url=TEST_DATABASE_URL)
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
            assert client.get("/").status_code == 404
            assert client.get("/login").status_code == 404
