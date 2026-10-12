"""Tests for session-only credential storage and its report/persistence contract."""

from __future__ import annotations

import json

import pytest

from app.credentials import (
    AdContext,
    CredentialStore,
    SqlCredentials,
    credential_summary,
)


class FakeReportBuilder:
    """Local double standing in for the report payload builder (#19)."""

    def __init__(self) -> None:
        self.payloads: list[object] = []

    def build(self, payload: object) -> str:
        self.payloads.append(payload)
        return json.dumps(payload, default=str)


class FakeRepositoryWriter:
    """Local double standing in for the repository write path (#21)."""

    def __init__(self) -> None:
        self.rows: list[object] = []

    def write(self, row: object) -> None:
        self.rows.append(row)


def test_store_returns_same_object_reference():
    store = CredentialStore()
    credentials = SqlCredentials(username="sa", password="supersecret")
    store.store("inv-1", credentials)
    assert store.get("inv-1") is credentials


def test_missing_investigation_returns_none():
    assert CredentialStore().get("missing") is None


def test_overwrite_same_investigation_keeps_one_entry():
    store = CredentialStore()
    store.store("inv-1", SqlCredentials("sa", "first-secret"))
    store.store("inv-1", SqlCredentials("sa", "second-secret"))
    assert len(store) == 1
    assert store.get("inv-1").password == "second-secret"


def test_clear_removes_entry():
    store = CredentialStore()
    store.store("inv-1", SqlCredentials("sa", "supersecret"))
    store.clear("inv-1")
    assert "inv-1" not in store
    assert store.get("inv-1") is None


def test_clear_twice_is_safe():
    store = CredentialStore()
    store.store("inv-1", SqlCredentials("sa", "supersecret"))
    store.clear("inv-1")
    store.clear("inv-1")
    assert len(store) == 0


def test_end_investigation_removes_and_is_idempotent():
    store = CredentialStore()
    store.store("inv-1", SqlCredentials("sa", "supersecret"))
    store.end_investigation("inv-1")
    store.end_investigation("inv-1")
    assert store.get("inv-1") is None


def test_ad_context_round_trips():
    store = CredentialStore()
    context = AdContext(
        keytab_path="/etc/krb5.keytab",
        principal="svc-sql@EXAMPLE.COM",
        spn="MSSQLSvc/host.example.com:1433",
    )
    store.store("inv-2", context)
    assert store.get("inv-2") is context


def test_stores_are_independent_instances():
    first = CredentialStore()
    second = CredentialStore()
    first.store("inv-1", SqlCredentials("sa", "supersecret"))
    assert "inv-1" not in second
    assert len(second) == 0


def test_store_does_not_expose_values_through_iteration():
    store = CredentialStore()
    store.store("inv-1", SqlCredentials("sa", "supersecret"))
    with pytest.raises(TypeError):
        dict(store)


def test_repr_never_shows_password():
    credentials = SqlCredentials("sa", "supersecret")
    assert "supersecret" not in repr(credentials)


def test_credential_summary_excludes_secrets():
    summary = credential_summary(SqlCredentials("sa", "supersecret"))
    assert summary == {"auth_mode": "sql"}
    assert "supersecret" not in repr(summary)
    assert "sa" not in repr(summary)
    assert credential_summary(AdContext("/k", "p", "s")) == {"auth_mode": "ad"}
    assert credential_summary(None) == {}


def test_credentials_never_reach_report_payload():
    store = CredentialStore()
    store.store("inv-1", SqlCredentials("sa", "supersecret"))
    payload = {
        "investigation_id": "inv-1",
        "auth": credential_summary(store.get("inv-1")),
    }

    builder = FakeReportBuilder()
    rendered = builder.build(payload)

    assert "supersecret" not in rendered
    assert all("supersecret" not in json.dumps(p, default=str) for p in builder.payloads)


def test_credentials_never_reach_repository_write_path():
    store = CredentialStore()
    store.store("inv-1", SqlCredentials("sa", "supersecret"))

    writer = FakeRepositoryWriter()
    writer.write(
        {
            "investigation_id": "inv-1",
            "auth": credential_summary(store.get("inv-1")),
        }
    )

    assert all("supersecret" not in json.dumps(row, default=str) for row in writer.rows)
