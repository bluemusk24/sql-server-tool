"""Tests for the investigation-create endpoint and its schemas.

The endpoint is exercised with FastAPI's ``TestClient`` and dependency
overrides: an in-memory repository stands in for the repository SQL Server and
a recording writer captures the audit event. No live SQL Server or Redis is
needed; the real SQLAlchemy repository is covered by the gated
``tests/test_investigations_db.py``.
"""

from __future__ import annotations

import dataclasses
import sys

import pytest
from pydantic import ValidationError

import app.investigations as investigations_package
from app.diagnostics.catalog import build_catalog
from app.investigations import ALLOWED_LOOKBACK_HOURS
from app.investigations.dependencies import (
    FIXTURE_ACTOR,
    FIXTURE_SSO_ISSUER,
    get_audit_writer,
    get_investigation_repository,
)
from app.investigations.repository import (
    InMemoryInvestigationRepository,
    InvestigationRecord,
    RecordingAuditWriter,
)
from app.investigations.schemas import InvestigationCreate

VALID_ENV = {
    "DATABASE_URL": "mssql+pyodbc://user:pass@db:1433/repo?driver=ODBC+Driver+18+for+SQL+Server",
    "REDIS_URL": "redis://localhost:6379/0",
    "OIDC_ISSUER": "https://issuer.example.com/",
    "OIDC_CLIENT_ID": "client",
    "OIDC_CLIENT_SECRET": "client-secret",
    "LLM_ENDPOINT": "https://llm.example.com/v1",
    "LLM_API_KEY": "llm-key",
}

FIXTURE_ORG_ID = 7

CREDENTIAL_FIELD_NAMES = {
    "password",
    "pwd",
    "secret",
    "api_key",
    "token",
    "username",
    "user",
    "connection_string",
    "conn_str",
}


def _settings():
    from app.config import Settings

    return Settings(
        database_url=VALID_ENV["DATABASE_URL"],
        redis_url=VALID_ENV["REDIS_URL"],
        oidc_issuer=VALID_ENV["OIDC_ISSUER"],
        oidc_client_id=VALID_ENV["OIDC_CLIENT_ID"],
        oidc_client_secret=VALID_ENV["OIDC_CLIENT_SECRET"],
        llm_endpoint=VALID_ENV["LLM_ENDPOINT"],
        llm_api_key=VALID_ENV["LLM_API_KEY"],
    )


@pytest.fixture
def repository() -> InMemoryInvestigationRepository:
    return InMemoryInvestigationRepository({FIXTURE_SSO_ISSUER: FIXTURE_ORG_ID})


@pytest.fixture
def audit() -> RecordingAuditWriter:
    return RecordingAuditWriter()


@pytest.fixture
def client(monkeypatch, repository, audit):
    from fastapi.testclient import TestClient

    for name, value in VALID_ENV.items():
        monkeypatch.setenv(name, value)
    sys.modules.pop("app.main", None)
    from app.main import create_app

    app = create_app(_settings())
    app.dependency_overrides[get_investigation_repository] = lambda: repository
    app.dependency_overrides[get_audit_writer] = lambda: audit
    return TestClient(app)


# --- single source of truth -------------------------------------------------


def test_allowed_lookback_hours_is_the_fixed_set():
    assert ALLOWED_LOOKBACK_HOURS == (1, 4, 8, 24)


def test_allowed_lookback_hours_is_exported_for_reuse():
    assert "ALLOWED_LOOKBACK_HOURS" in investigations_package.__all__


# --- schema validation ------------------------------------------------------


def test_valid_payload_defaults():
    payload = InvestigationCreate(server_host="sql01.example.com", lookback_hours=1)
    assert payload.server_host == "sql01.example.com"
    assert payload.server_port == 1433
    assert payload.auth_type.value == "sql"
    assert payload.databases is None
    assert payload.lookback_hours == 1


@pytest.mark.parametrize("lookback", sorted(ALLOWED_LOOKBACK_HOURS))
def test_every_allowed_lookback_is_accepted(lookback):
    payload = InvestigationCreate(server_host="sql01", lookback_hours=lookback)
    assert payload.lookback_hours == lookback


@pytest.mark.parametrize("lookback", [0, 2, 3, 5, 12, 48, -1])
def test_lookback_outside_allowed_set_is_rejected_with_allowed_values(lookback):
    with pytest.raises(ValidationError) as exc_info:
        InvestigationCreate(server_host="sql01", lookback_hours=lookback)
    message = str(exc_info.value)
    assert "1, 4, 8, 24" in message


def test_empty_databases_list_is_rejected():
    with pytest.raises(ValidationError, match="non-empty list"):
        InvestigationCreate(
            server_host="sql01", databases=[], lookback_hours=1
        )


def test_blank_database_name_is_rejected():
    with pytest.raises(ValidationError, match="non-empty strings"):
        InvestigationCreate(
            server_host="sql01", databases=["db1", "   "], lookback_hours=1
        )


def test_database_names_are_trimmed():
    payload = InvestigationCreate(
        server_host="sql01", databases=["  db1  ", "db2"], lookback_hours=4
    )
    assert payload.databases == ["db1", "db2"]


@pytest.mark.parametrize("host", ["", "   ", "bad host", "host;evil", "http://x/y"])
def test_malformed_host_is_rejected(host):
    with pytest.raises(ValidationError):
        InvestigationCreate(server_host=host, lookback_hours=1)


@pytest.mark.parametrize("port", [0, -1, 65536, 99999])
def test_out_of_range_port_is_rejected(port):
    with pytest.raises(ValidationError):
        InvestigationCreate(server_host="sql01", server_port=port, lookback_hours=1)


def test_named_instance_and_ipv6_hosts_are_accepted():
    assert InvestigationCreate(
        server_host=r"HOST\SQLEXPRESS", lookback_hours=1
    ).server_host == r"HOST\SQLEXPRESS"
    assert InvestigationCreate(
        server_host="[::1]", lookback_hours=1
    ).server_host == "[::1]"


def test_credentials_are_not_accepted_on_the_endpoint_schema():
    with pytest.raises(ValidationError):
        InvestigationCreate(
            server_host="sql01",
            lookback_hours=1,
            password="s3cret",  # type: ignore[call-arg]
        )


# --- model ------------------------------------------------------------------


def test_investigation_model_has_no_credential_columns():
    from app.db.models import Investigation

    columns = set(Investigation.__table__.columns.keys())
    assert columns == {
        "id",
        "organization_id",
        "server_host",
        "server_port",
        "auth_type",
        "scope_kind",
        "databases",
        "lookback_hours",
        "status",
        "created_at",
    }
    assert columns & CREDENTIAL_FIELD_NAMES == set()


def test_investigation_record_has_no_credential_fields():
    names = {field.name for field in dataclasses.fields(InvestigationRecord)}
    assert names & CREDENTIAL_FIELD_NAMES == set()


# --- endpoint ---------------------------------------------------------------


def test_create_whole_instance_investigation_returns_fixed_shape(client, repository):
    response = client.post(
        "/investigations",
        json={"server_host": "sql01.example.com", "lookback_hours": 4},
    )

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"id", "status", "catalog"}
    assert body["status"] == "draft"
    assert isinstance(body["id"], int)

    built = build_catalog().all()
    assert [entry["id"] for entry in body["catalog"]] == [q.id for q in built]
    assert all(entry["read_only"] is True for entry in body["catalog"])

    assert len(repository.rows) == 1
    row = repository.rows[0]
    assert row.organization_id == FIXTURE_ORG_ID
    assert row.scope_kind == "instance"
    assert row.databases is None
    assert row.server_port == 1433
    assert row.status == "draft"


def test_create_database_scoped_investigation(client, repository):
    response = client.post(
        "/investigations",
        json={
            "server_host": "sql01",
            "server_port": 1444,
            "databases": ["Sales", "Warehouse"],
            "lookback_hours": 8,
        },
    )

    assert response.status_code == 201
    row = repository.rows[0]
    assert row.scope_kind == "databases"
    assert row.databases == ["Sales", "Warehouse"]
    assert row.server_port == 1444
    assert row.lookback_hours == 8


@pytest.mark.parametrize("lookback", [2, 0, 48])
def test_unsupported_lookback_returns_422_with_allowed_values(client, lookback):
    response = client.post(
        "/investigations",
        json={"server_host": "sql01", "lookback_hours": lookback},
    )
    assert response.status_code == 422
    assert "1, 4, 8, 24" in response.text


def test_empty_databases_returns_422(client):
    response = client.post(
        "/investigations",
        json={"server_host": "sql01", "databases": [], "lookback_hours": 1},
    )
    assert response.status_code == 422


def test_ad_auth_returns_501(client):
    response = client.post(
        "/investigations",
        json={"server_host": "sql01", "auth_type": "ad", "lookback_hours": 1},
    )
    assert response.status_code == 501
    assert "not supported" in response.text


def test_password_in_body_returns_422(client):
    response = client.post(
        "/investigations",
        json={"server_host": "sql01", "lookback_hours": 1, "password": "s3cret"},
    )
    assert response.status_code == 422


def test_unknown_organization_returns_403(client):
    response = client.post(
        "/investigations",
        headers={"X-SSO-Issuer": "https://nobody.example.com/"},
        json={"server_host": "sql01", "lookback_hours": 1},
    )
    assert response.status_code == 403


def test_audit_row_is_written_for_creation(client, audit, repository):
    response = client.post(
        "/investigations",
        headers={"X-Actor": "dba@example.com"},
        json={"server_host": "sql01", "lookback_hours": 24},
    )

    assert response.status_code == 201
    assert len(audit.entries) == 1
    entry = audit.entries[0]
    assert entry.action == "investigation.create"
    assert entry.actor == "dba@example.com"
    assert entry.organization_id == FIXTURE_ORG_ID
    assert entry.target == f"investigation:{repository.rows[0].id}"


def test_default_actor_is_used_when_header_absent(client, audit):
    client.post(
        "/investigations", json={"server_host": "sql01", "lookback_hours": 1}
    )
    assert audit.entries[0].actor == FIXTURE_ACTOR


def test_password_never_echoed_in_response(client):
    response = client.post(
        "/investigations",
        json={"server_host": "sql01", "lookback_hours": 1},
    )
    assert "password" not in response.text.lower()
    assert "pwd" not in response.text.lower()


def test_endpoint_works_with_deferred_audit_writer(monkeypatch, repository):
    """The default (deferred, no-op) audit writer must not break creation."""
    from fastapi.testclient import TestClient

    for name, value in VALID_ENV.items():
        monkeypatch.setenv(name, value)
    sys.modules.pop("app.main", None)
    from app.main import create_app

    app = create_app(_settings())
    app.dependency_overrides[get_investigation_repository] = lambda: repository
    app.state.audit_writer = None
    client = TestClient(app)

    response = client.post(
        "/investigations", json={"server_host": "sql01", "lookback_hours": 1}
    )
    assert response.status_code == 201


# --- service ----------------------------------------------------------------


def test_service_resolves_organization_from_issuer():
    from app.investigations.schemas import InvestigationCreate
    from app.investigations.service import create_investigation

    repository = InMemoryInvestigationRepository({FIXTURE_SSO_ISSUER: 42})
    audit = RecordingAuditWriter()
    payload = InvestigationCreate(server_host="sql01", lookback_hours=1)

    record = create_investigation(
        payload,
        caller_issuer=FIXTURE_SSO_ISSUER,
        caller_actor="dba",
        repository=repository,
        audit_writer=audit,
    )

    assert record.organization_id == 42
    assert record.status == "draft"
