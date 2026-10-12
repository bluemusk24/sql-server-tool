"""Unit tests for organization provisioning (no live SQL Server required).

The provisioning routine is exercised with a scripted fake session and fake
engines: database identifiers must be built from the organization id, the org
row must be inserted and flushed, CREATE DATABASE and the migrations must be
attempted, and a failed migration must drop the freshly created database.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Self

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app import provisioning
from app.provisioning import organization_database_name, provision_organization

BASE_URL = (
    "mssql+pyodbc://user:pass@db:1433/repo"
    "?driver=ODBC+Driver+18+for+SQL+Server"
)


class FakeSession:
    """Minimal session double that assigns an id on flush."""

    def __init__(self, assigned_id: int = 1, flush_error: Exception | None = None):
        self.assigned_id = assigned_id
        self.flush_error = flush_error
        self.added: list[object] = []
        self.flush_calls = 0

    def add(self, instance: object) -> None:
        self.added.append(instance)

    def flush(self) -> None:
        self.flush_calls += 1
        if self.flush_error is not None:
            raise self.flush_error
        for instance in self.added:
            if getattr(instance, "id", None) is None:
                instance.id = self.assigned_id

    def get_bind(self) -> object:
        return object()


class FakeConnection:
    def __init__(self, executed: list[str]):
        self.executed = executed

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def execute(self, statement: object) -> None:
        self.executed.append(str(statement))


class FakeEngine:
    def __init__(self, executed: list[str] | None = None):
        self.executed = executed if executed is not None else []
        self.disposed = False
        self.url = make_url(BASE_URL)

    def connect(self) -> FakeConnection:
        return FakeConnection(self.executed)

    def dispose(self) -> None:
        self.disposed = True


class FakeConfig:
    def __init__(self, path: str):
        self.path = path
        self.options: dict[str, str] = {}

    def set_main_option(self, key: str, value: str) -> None:
        self.options[key] = value


class FakeCommand:
    def __init__(self):
        self.upgrades: list[tuple[FakeConfig, str]] = []

    def upgrade(self, config: FakeConfig, revision: str) -> None:
        self.upgrades.append((config, revision))


def test_organization_database_name_is_derived_from_id():
    assert organization_database_name(1) == "ssda_org_1"
    assert organization_database_name(42) == "ssda_org_42"


@pytest.mark.parametrize("organization_id", [0, -1])
def test_organization_database_name_rejects_non_positive(organization_id):
    with pytest.raises(ValueError):
        organization_database_name(organization_id)


def test_provision_inserts_row_creates_database_and_migrates(monkeypatch):
    session = FakeSession(assigned_id=7)
    base_engine = FakeEngine()
    created: list[tuple[FakeEngine, str]] = []
    migrated: list[tuple[FakeEngine, str]] = []
    dropped: list[tuple[FakeEngine, str]] = []

    monkeypatch.setattr(provisioning, "_get_engine", lambda _session: base_engine)
    monkeypatch.setattr(
        provisioning,
        "_create_database",
        lambda engine, name: created.append((engine, name)),
    )
    monkeypatch.setattr(
        provisioning,
        "_run_migrations",
        lambda engine, name: migrated.append((engine, name)),
    )
    monkeypatch.setattr(
        provisioning,
        "_drop_database",
        lambda engine, name: dropped.append((engine, name)),
    )

    organization = provision_organization(
        session, "Acme", "https://issuer.example.com/acme"
    )

    assert session.flush_calls == 1
    assert session.added == [organization]
    assert organization.name == "Acme"
    assert organization.sso_issuer == "https://issuer.example.com/acme"
    assert created == [(base_engine, "ssda_org_7")]
    assert migrated == [(base_engine, "ssda_org_7")]
    assert dropped == []


def test_provision_drops_database_when_migration_fails(monkeypatch):
    session = FakeSession(assigned_id=9)
    base_engine = FakeEngine()
    created: list[str] = []
    dropped: list[str] = []

    def boom(_engine, _name):
        raise RuntimeError("migration exploded")

    monkeypatch.setattr(provisioning, "_get_engine", lambda _session: base_engine)
    monkeypatch.setattr(
        provisioning, "_create_database", lambda _engine, name: created.append(name)
    )
    monkeypatch.setattr(provisioning, "_run_migrations", boom)
    monkeypatch.setattr(
        provisioning, "_drop_database", lambda _engine, name: dropped.append(name)
    )

    with pytest.raises(RuntimeError, match="migration exploded"):
        provision_organization(session, "Acme", "https://issuer.example.com/acme")

    assert created == ["ssda_org_9"]
    assert dropped == ["ssda_org_9"]


def test_provision_duplicate_does_not_create_database(monkeypatch):
    session = FakeSession(
        flush_error=IntegrityError("stmt", {}, Exception("duplicate name"))
    )
    created: list[str] = []
    monkeypatch.setattr(
        provisioning, "_create_database", lambda _engine, name: created.append(name)
    )

    with pytest.raises(IntegrityError):
        provision_organization(session, "Acme", "https://issuer.example.com/acme")

    assert created == []


def test_create_database_targets_master_with_autocommit(monkeypatch):
    admin = FakeEngine()
    captured: dict[str, object] = {}

    def fake_create_engine(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return admin

    monkeypatch.setattr(provisioning, "create_engine", fake_create_engine)
    base_engine = fake_engine_with_url(BASE_URL)

    provisioning._create_database(base_engine, "ssda_org_3")

    assert captured["url"].database == "master"
    assert captured["kwargs"]["isolation_level"] == "AUTOCOMMIT"
    assert admin.executed == ["CREATE DATABASE [ssda_org_3]"]
    assert admin.disposed is True


def test_drop_database_forces_single_user_and_drops(monkeypatch):
    admin = FakeEngine()
    captured: dict[str, object] = {}

    def fake_create_engine(url, **kwargs):
        captured["url"] = url
        return admin

    monkeypatch.setattr(provisioning, "create_engine", fake_create_engine)
    base_engine = fake_engine_with_url(BASE_URL)

    provisioning._drop_database(base_engine, "ssda_org_3")

    assert captured["url"].database == "master"
    assert admin.executed == [
        "ALTER DATABASE [ssda_org_3] SET SINGLE_USER WITH ROLLBACK IMMEDIATE",
        "DROP DATABASE [ssda_org_3]",
    ]
    assert admin.disposed is True


def test_run_migrations_points_alembic_at_target_database(monkeypatch):
    command = FakeCommand()
    monkeypatch.setattr(provisioning, "Config", FakeConfig)
    monkeypatch.setattr(provisioning, "command", command)
    base_engine = fake_engine_with_url(BASE_URL)

    provisioning._run_migrations(base_engine, "ssda_org_9")

    assert len(command.upgrades) == 1
    config, revision = command.upgrades[0]
    assert revision == "head"
    assert "ssda_org_9" in config.options["sqlalchemy.url"]
    assert "ODBC+Driver+18+for+SQL+Server" in config.options["sqlalchemy.url"]


def test_get_engine_rejects_non_engine_bind():
    class NoEngineSession:
        def get_bind(self) -> object:
            return object()

    with pytest.raises(TypeError):
        provisioning._get_engine(NoEngineSession())


def fake_engine_with_url(url: str) -> SimpleNamespace:
    return SimpleNamespace(url=make_url(url))
