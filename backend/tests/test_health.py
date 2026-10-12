import importlib
import sys

import pytest

from app.config import REQUIRED_ENV_NAMES

VALID_ENV = {
    "DATABASE_URL": "mssql+pyodbc://user:pass@db:1433/repo?driver=ODBC+Driver+18+for+SQL+Server",
    "REDIS_URL": "redis://localhost:6379/0",
    "OIDC_ISSUER": "https://issuer.example.com/",
    "OIDC_CLIENT_ID": "client",
    "OIDC_CLIENT_SECRET": "client-secret",
    "LLM_ENDPOINT": "https://llm.example.com/v1",
    "LLM_API_KEY": "llm-key",
}


def _fresh_app_main():
    """Re-import app.main so its startup settings load under the current env."""
    sys.modules.pop("app.main", None)
    return importlib.import_module("app.main")


@pytest.fixture
def isolated_config(monkeypatch, tmp_path):
    from app import config

    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    for name in REQUIRED_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)


def test_app_main_fails_fast_without_settings(isolated_config):
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        _fresh_app_main()


def test_health_returns_200(isolated_config, monkeypatch):
    for name, value in VALID_ENV.items():
        monkeypatch.setenv(name, value)

    from fastapi.testclient import TestClient

    app_module = _fresh_app_main()
    client = TestClient(app_module.app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0"}