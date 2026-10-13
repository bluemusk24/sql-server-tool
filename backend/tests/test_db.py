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
