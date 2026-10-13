"""Application wiring tests: CORS origin configuration (#32).

The deployed frontend (Vercel) calls the API from its own origin, so CORS
must be configurable via ``$CORS_ALLOW_ORIGINS`` while defaulting to the
local Vite dev servers that `make run` starts.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import DEV_ORIGINS, allowed_origins, create_app

TEST_DATABASE_URL = "sqlite:///:memory:"


def test_cors_defaults_to_the_dev_servers(monkeypatch) -> None:
    monkeypatch.delenv("CORS_ALLOW_ORIGINS", raising=False)
    assert allowed_origins() == DEV_ORIGINS


def test_cors_reads_a_comma_separated_override(monkeypatch) -> None:
    monkeypatch.setenv(
        "CORS_ALLOW_ORIGINS", "https://app.vercel.app, https://api.example.com"
    )
    assert allowed_origins() == ["https://app.vercel.app", "https://api.example.com"]


def test_cors_ignores_blank_entries(monkeypatch) -> None:
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", " , https://app.vercel.app ,")
    assert allowed_origins() == ["https://app.vercel.app"]


def test_configured_origin_gets_cors_headers_and_others_do_not(monkeypatch) -> None:
    """A request from the deployed origin is allowed; an unknown one isn't."""
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://app.vercel.app")
    app = create_app(database_url=TEST_DATABASE_URL)
    with TestClient(app) as client:
        allowed = client.get("/health", headers={"Origin": "https://app.vercel.app"})
        assert allowed.status_code == 200
        assert (
            allowed.headers.get("access-control-allow-origin")
            == "https://app.vercel.app"
        )

        denied = client.get("/health", headers={"Origin": "https://evil.example.com"})
        assert denied.status_code == 200
        assert "access-control-allow-origin" not in denied.headers


def test_dev_origins_still_work_with_no_override(monkeypatch) -> None:
    """`make run` (Vite on :5173) keeps working with zero configuration."""
    monkeypatch.delenv("CORS_ALLOW_ORIGINS", raising=False)
    app = create_app(database_url=TEST_DATABASE_URL)
    with TestClient(app) as client:
        response = client.get("/health", headers={"Origin": "http://localhost:5173"})
        assert response.status_code == 200
        assert (
            response.headers.get("access-control-allow-origin")
            == "http://localhost:5173"
        )
