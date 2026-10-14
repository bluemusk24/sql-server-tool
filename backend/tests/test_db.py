"""Database URL resolution: SDIP_DATABASE_URL > DATABASE_URL > default."""

from __future__ import annotations

import pytest

from app import db


def test_sdip_variable_wins_when_both_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SDIP_DATABASE_URL", "sqlite:////data/sdip.db")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./other.db")
    assert db.get_database_url() == "sqlite:////data/sdip.db"


def test_database_url_still_works_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SDIP_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./other.db")
    assert db.get_database_url() == "sqlite:///./other.db"


def test_default_is_sqlite_when_nothing_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SDIP_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert db.get_database_url() == db.DEFAULT_DATABASE_URL


# ── Postgres driver pinning ──────────────────────────────────────────


def test_bare_postgres_url_gets_psycopg_driver() -> None:
    assert (
        db.normalize_database_url("postgresql://sdip:sdip@localhost:5432/sdip")
        == "postgresql+psycopg://sdip:sdip@localhost:5432/sdip"
    )


def test_legacy_postgres_alias_is_modernized() -> None:
    assert (
        db.normalize_database_url("postgres://u:p@host/db")
        == "postgresql+psycopg://u:p@host/db"
    )


def test_explicit_driver_is_preserved() -> None:
    url = "postgresql+psycopg://u:p@host/db"
    assert db.normalize_database_url(url) == url


def test_sqlite_urls_are_untouched() -> None:
    assert (
        db.normalize_database_url("sqlite:////data/sdip.db")
        == "sqlite:////data/sdip.db"
    )


def test_get_database_url_normalizes_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "SDIP_DATABASE_URL", "postgresql://sdip:sdip@localhost:5432/sdip"
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert (
        db.get_database_url()
        == "postgresql+psycopg://sdip:sdip@localhost:5432/sdip"
    )


def test_make_engine_uses_postgresql_dialect() -> None:
    # create_engine is lazy: the dialect is chosen without connecting.
    engine = db.make_engine("postgresql+psycopg://u:p@localhost/db")
    assert engine.dialect.name == "postgresql"
