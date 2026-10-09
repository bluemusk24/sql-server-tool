"""Unit tests for the app.db engine/session wiring.

These tests never open a connection and never import the ODBC DBAPI; they assert
that the helpers read the URL from settings and defer engine creation.
"""

from unittest.mock import Mock

from app.config import Settings
from app.db import build_engine, build_session_factory
from app.db import engine as engine_module
from app.db import session as session_module

DATABASE_URL = (
    "mssql+pyodbc://user:pass@db:1433/repo?driver=ODBC+Driver+18+for+SQL+Server"
)


def _settings() -> Settings:
    return Settings(
        database_url=DATABASE_URL,
        redis_url="redis://localhost:6379/0",
        oidc_issuer="https://issuer.example.com/",
        oidc_client_id="client",
        oidc_client_secret="client-secret",
        llm_endpoint="https://llm.example.com/v1",
        llm_api_key="llm-key",
    )


def test_package_reexports_helpers():
    assert build_engine is engine_module.build_engine
    assert build_session_factory is session_module.build_session_factory


def test_build_engine_uses_settings_database_url(monkeypatch):
    captured: dict[str, str] = {}

    def fake_create_engine(url, *args, **kwargs):
        captured["url"] = url
        return Mock(name="engine")

    monkeypatch.setattr(engine_module, "create_engine", fake_create_engine)

    engine = build_engine(_settings())

    assert captured["url"] == DATABASE_URL
    assert engine is not None


def test_get_engine_is_lazy_and_reads_settings(monkeypatch):
    sentinel = Mock(name="engine")
    monkeypatch.setattr(engine_module, "_engine", None)
    monkeypatch.setattr(engine_module, "load_settings", _settings)
    monkeypatch.setattr(engine_module, "build_engine", lambda settings: sentinel)

    assert engine_module._engine is None
    assert engine_module.get_engine() is sentinel
    assert engine_module.get_engine() is sentinel


def test_build_session_factory_binds_engine():
    engine = Mock(name="engine")

    factory = build_session_factory(engine)

    assert factory.kw["bind"] is engine
