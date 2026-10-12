"""Tests for the query approval endpoints and the run gate.

Exercised with FastAPI's ``TestClient`` and dependency overrides: in-memory
repositories stand in for SQL Server and a recording writer captures audit
events. The append-only rule is checked both through the in-memory repository
and at the SQLAlchemy model level with an in-memory SQLite engine. No live SQL
Server or Redis is needed; the real SQLAlchemy repository is covered by the
gated ``tests/test_approvals_db.py``.
"""

from __future__ import annotations

import dataclasses
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects import mssql
from sqlalchemy.schema import CreateTable

from app.db.models import AppendOnlyViolation, Approval, Base
from app.db.session import build_session_factory
from app.diagnostics.catalog import Catalog, DiagnosticQuery, build_catalog
from app.investigations.dependencies import (
    FIXTURE_ACTOR,
    FIXTURE_SSO_ISSUER,
    get_approval_repository,
    get_audit_writer,
    get_catalog,
    get_investigation_repository,
)
from app.investigations.repository import (
    ApprovalRecord,
    InMemoryApprovalRepository,
    InMemoryInvestigationRepository,
    RecordingAuditWriter,
)
from app.investigations.schemas import ApprovalState
from app.investigations.service import (
    APPROVAL_ACTION,
    can_start,
    compute_state,
    required_catalog_ids,
)

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
def approvals() -> InMemoryApprovalRepository:
    return InMemoryApprovalRepository()


@pytest.fixture
def audit() -> RecordingAuditWriter:
    return RecordingAuditWriter()


def _build_client(monkeypatch, repository, approvals, audit, catalog=None):
    from fastapi.testclient import TestClient

    for name, value in VALID_ENV.items():
        monkeypatch.setenv(name, value)
    sys.modules.pop("app.main", None)
    from app.main import create_app

    app = create_app(_settings())
    app.dependency_overrides[get_investigation_repository] = lambda: repository
    app.dependency_overrides[get_approval_repository] = lambda: approvals
    app.dependency_overrides[get_audit_writer] = lambda: audit
    if catalog is not None:
        app.dependency_overrides[get_catalog] = lambda: catalog
    return TestClient(app)


@pytest.fixture
def client(monkeypatch, repository, approvals, audit):
    return _build_client(monkeypatch, repository, approvals, audit)


def _create_investigation(client) -> int:
    response = client.post(
        "/investigations", json={"server_host": "sql01", "lookback_hours": 1}
    )
    assert response.status_code == 201
    return response.json()["id"]


def _required_ids() -> list[str]:
    return required_catalog_ids(build_catalog())


def _optional_catalog() -> Catalog:
    catalog = Catalog()
    catalog.register(
        DiagnosticQuery(
            id="required_query",
            title="Required",
            description="A query that needs approval.",
            category="blocking",
            issue_area="blocking",
            sql="SELECT 1 AS one",
            expected_impact="Minimal.",
            versions=("2008+",),
            min_permission="VIEW SERVER STATE",
            read_only=True,
            requires_approval=True,
        )
    )
    catalog.register(
        DiagnosticQuery(
            id="optional_query",
            title="Optional",
            description="A query that needs no approval.",
            category="blocking",
            issue_area="blocking",
            sql="SELECT 2 AS two",
            expected_impact="Minimal.",
            versions=("2008+",),
            min_permission="VIEW SERVER STATE",
            read_only=True,
            requires_approval=False,
        )
    )
    return catalog


# --- required set / state computation --------------------------------------


def test_required_set_is_the_catalog_approval_queries():
    ids = _required_ids()
    assert ids == [entry.id for entry in build_catalog().all()]
    assert len(ids) == 8


def test_compute_state_pending_approved_rejected():
    approved = ApprovalRecord(1, 1, "a", "approved", "dba", None, None)
    rejected = ApprovalRecord(2, 1, "b", "rejected", "dba", None, None)

    assert compute_state({}, ["a", "b"]) is ApprovalState.pending
    assert (
        compute_state({"a": approved, "b": approved}, ["a", "b"])
        is ApprovalState.approved
    )
    assert (
        compute_state({"a": approved, "b": rejected}, ["a", "b"])
        is ApprovalState.rejected
    )


# --- GET --------------------------------------------------------------------


def test_unknown_investigation_get_is_404(client):
    response = client.get("/investigations/999/approvals")
    assert response.status_code == 404


def test_get_initial_state_is_pending_with_all_undecided(client):
    investigation_id = _create_investigation(client)
    response = client.get(f"/investigations/{investigation_id}/approvals")

    assert response.status_code == 200
    body = response.json()
    assert body["investigation_id"] == investigation_id
    assert body["state"] == "pending"
    assert [q["catalog_id"] for q in body["queries"]] == _required_ids()
    assert all(
        q["decision"] is None and q["actor"] is None and q["note"] is None
        for q in body["queries"]
    )


# --- POST happy paths -------------------------------------------------------


def test_partial_approval_leaves_pending(client):
    investigation_id = _create_investigation(client)
    response = client.post(
        f"/investigations/{investigation_id}/approvals",
        json={"decisions": {_required_ids()[0]: "approved"}},
    )
    assert response.status_code == 200
    assert response.json()["state"] == "pending"


def test_completing_all_queries_yields_approved(client):
    investigation_id = _create_investigation(client)
    decisions = {catalog_id: "approved" for catalog_id in _required_ids()}
    response = client.post(
        f"/investigations/{investigation_id}/approvals",
        json={"decisions": decisions},
    )
    assert response.status_code == 200
    assert response.json()["state"] == "approved"

    listing = client.get(f"/investigations/{investigation_id}/approvals")
    assert listing.json()["state"] == "approved"


def test_single_rejection_flips_state_to_rejected(client):
    investigation_id = _create_investigation(client)
    ids = _required_ids()
    decisions = {catalog_id: "approved" for catalog_id in ids[1:]}
    decisions[ids[0]] = "rejected"
    response = client.post(
        f"/investigations/{investigation_id}/approvals",
        json={"decisions": decisions},
    )
    assert response.json()["state"] == "rejected"


def test_rejection_dominates_with_undecided_queries(client):
    investigation_id = _create_investigation(client)
    response = client.post(
        f"/investigations/{investigation_id}/approvals",
        json={"decisions": {_required_ids()[0]: "rejected"}},
    )
    assert response.json()["state"] == "rejected"


def test_note_is_stored_and_returned(client):
    investigation_id = _create_investigation(client)
    catalog_id = _required_ids()[0]
    response = client.post(
        f"/investigations/{investigation_id}/approvals",
        json={"decisions": {catalog_id: "approved"}, "note": "  looks safe  "},
    )
    assert response.status_code == 200
    stored = next(
        q for q in response.json()["queries"] if q["catalog_id"] == catalog_id
    )
    assert stored["decision"] == "approved"
    assert stored["note"] == "looks safe"


def test_post_response_matches_get(client):
    investigation_id = _create_investigation(client)
    decisions = {catalog_id: "approved" for catalog_id in _required_ids()}
    posted = client.post(
        f"/investigations/{investigation_id}/approvals",
        json={"decisions": decisions},
    ).json()
    fetched = client.get(f"/investigations/{investigation_id}/approvals").json()
    assert posted == fetched


# --- idempotency and append-only -------------------------------------------


def test_repeating_same_decision_is_idempotent_and_appends_nothing(
    client, approvals, audit
):
    investigation_id = _create_investigation(client)
    catalog_id = _required_ids()[0]
    payload = {"decisions": {catalog_id: "approved"}}

    first = client.post(
        f"/investigations/{investigation_id}/approvals", json=payload
    )
    second = client.post(
        f"/investigations/{investigation_id}/approvals", json=payload
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(approvals.rows) == 1
    approval_entries = [
        entry for entry in audit.entries if entry.action == APPROVAL_ACTION
    ]
    assert len(approval_entries) == 1


def test_flip_while_draft_appends_new_row(client, approvals):
    investigation_id = _create_investigation(client)
    catalog_id = _required_ids()[0]

    client.post(
        f"/investigations/{investigation_id}/approvals",
        json={"decisions": {catalog_id: "approved"}},
    )
    response = client.post(
        f"/investigations/{investigation_id}/approvals",
        json={"decisions": {catalog_id: "rejected"}},
    )

    assert response.status_code == 200
    assert response.json()["state"] == "rejected"
    assert len(approvals.rows) == 2
    assert approvals.rows[0].decision == "approved"
    assert approvals.rows[1].decision == "rejected"


def test_decision_change_after_run_started_is_409(client, repository, approvals):
    investigation_id = _create_investigation(client)
    catalog_id = _required_ids()[0]
    client.post(
        f"/investigations/{investigation_id}/approvals",
        json={"decisions": {catalog_id: "approved"}},
    )
    repository.set_status(investigation_id, "running")

    response = client.post(
        f"/investigations/{investigation_id}/approvals",
        json={"decisions": {catalog_id: "rejected"}},
    )

    assert response.status_code == 409
    assert len(approvals.rows) == 1


def test_new_decision_after_run_started_is_409(client, repository):
    investigation_id = _create_investigation(client)
    repository.set_status(investigation_id, "running")
    response = client.post(
        f"/investigations/{investigation_id}/approvals",
        json={"decisions": {_required_ids()[0]: "approved"}},
    )
    assert response.status_code == 409


def test_audit_entry_written_per_appended_decision(client, audit):
    investigation_id = _create_investigation(client)
    ids = _required_ids()[:2]
    client.post(
        f"/investigations/{investigation_id}/approvals",
        json={"decisions": {catalog_id: "approved" for catalog_id in ids}},
    )
    approval_entries = [
        entry for entry in audit.entries if entry.action == APPROVAL_ACTION
    ]
    assert len(approval_entries) == 2
    assert approval_entries[0].actor == FIXTURE_ACTOR
    assert approval_entries[0].target.startswith(
        f"investigation:{investigation_id}/query:"
    )


# --- POST validation --------------------------------------------------------


def test_unknown_investigation_post_is_404(client):
    response = client.post(
        "/investigations/999/approvals",
        json={"decisions": {"blocking_current_chains": "approved"}},
    )
    assert response.status_code == 404


def test_empty_decisions_is_422(client):
    investigation_id = _create_investigation(client)
    response = client.post(
        f"/investigations/{investigation_id}/approvals", json={"decisions": {}}
    )
    assert response.status_code == 422


def test_unknown_catalog_id_is_422(client):
    investigation_id = _create_investigation(client)
    response = client.post(
        f"/investigations/{investigation_id}/approvals",
        json={"decisions": {"does_not_exist": "approved"}},
    )
    assert response.status_code == 422


def test_invalid_decision_value_is_422(client):
    investigation_id = _create_investigation(client)
    response = client.post(
        f"/investigations/{investigation_id}/approvals",
        json={"decisions": {_required_ids()[0]: "maybe"}},
    )
    assert response.status_code == 422


def test_decision_on_query_not_requiring_approval_is_422(
    monkeypatch, repository, approvals, audit
):
    client = _build_client(
        monkeypatch, repository, approvals, audit, catalog=_optional_catalog()
    )
    investigation_id = _create_investigation(client)

    response = client.post(
        f"/investigations/{investigation_id}/approvals",
        json={"decisions": {"optional_query": "approved"}},
    )
    assert response.status_code == 422


def test_optional_catalog_only_gates_required_queries(
    monkeypatch, repository, approvals, audit
):
    client = _build_client(
        monkeypatch, repository, approvals, audit, catalog=_optional_catalog()
    )
    investigation_id = _create_investigation(client)

    listing = client.get(f"/investigations/{investigation_id}/approvals").json()
    assert [q["catalog_id"] for q in listing["queries"]] == ["required_query"]

    response = client.post(
        f"/investigations/{investigation_id}/approvals",
        json={"decisions": {"required_query": "approved"}},
    )
    assert response.json()["state"] == "approved"


def test_password_in_body_is_422(client):
    investigation_id = _create_investigation(client)
    response = client.post(
        f"/investigations/{investigation_id}/approvals",
        json={
            "decisions": {_required_ids()[0]: "approved"},
            "password": "s3cret",
        },
    )
    assert response.status_code == 422


def test_password_never_echoed(client):
    investigation_id = _create_investigation(client)
    response = client.get(f"/investigations/{investigation_id}/approvals")
    assert "password" not in response.text.lower()
    assert "pwd" not in response.text.lower()


# --- run gate ---------------------------------------------------------------


def _seed_investigation(repository) -> int:
    record = repository.create_investigation(
        organization_id=FIXTURE_ORG_ID,
        server_host="sql01",
        server_port=1433,
        auth_type="sql",
        scope_kind="instance",
        databases=None,
        lookback_hours=1,
        status="draft",
    )
    return record.id


def test_can_start_gate_rejects_pending_approved_and_rejected(repository, approvals):
    catalog = build_catalog()
    investigation_id = _seed_investigation(repository)
    ids = required_catalog_ids(catalog)

    assert (
        can_start(
            investigation_id,
            investigation_repository=repository,
            approval_repository=approvals,
            catalog=catalog,
        )
        is False
    )

    for catalog_id in ids[:-1]:
        approvals.append_decision(
            investigation_id=investigation_id,
            catalog_id=catalog_id,
            decision="approved",
            actor="dba",
            note=None,
        )
    assert (
        can_start(
            investigation_id,
            investigation_repository=repository,
            approval_repository=approvals,
            catalog=catalog,
        )
        is False
    )

    approvals.append_decision(
        investigation_id=investigation_id,
        catalog_id=ids[-1],
        decision="approved",
        actor="dba",
        note=None,
    )
    assert (
        can_start(
            investigation_id,
            investigation_repository=repository,
            approval_repository=approvals,
            catalog=catalog,
        )
        is True
    )

    approvals.append_decision(
        investigation_id=investigation_id,
        catalog_id=ids[0],
        decision="rejected",
        actor="dba",
        note=None,
    )
    assert (
        can_start(
            investigation_id,
            investigation_repository=repository,
            approval_repository=approvals,
            catalog=catalog,
        )
        is False
    )


def test_can_start_false_for_unknown_investigation(repository, approvals):
    assert (
        can_start(
            999,
            investigation_repository=repository,
            approval_repository=approvals,
            catalog=build_catalog(),
        )
        is False
    )


# --- model ------------------------------------------------------------------


def test_approval_model_columns_and_constraints():
    table = Approval.__table__
    assert set(table.columns.keys()) == {
        "id",
        "investigation_id",
        "catalog_id",
        "decision",
        "actor",
        "note",
        "created_at",
    }
    constraint_names = {constraint.name for constraint in table.constraints}
    assert "ck_approval_decision" in constraint_names
    assert "ix_approval_investigation_id" in {index.name for index in table.indexes}


def test_approval_rows_are_append_only_at_the_model_level():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = build_session_factory(engine)

    with factory() as session:
        row = Approval(
            id=1,
            investigation_id=1,
            catalog_id="blocking_current_chains",
            decision="approved",
            actor="dba",
        )
        session.add(row)
        session.commit()
        row_id = row.id

    with factory() as session:
        stored = session.get(Approval, row_id)
        stored.decision = "rejected"
        with pytest.raises(AppendOnlyViolation):
            session.flush()
        session.rollback()

    with factory() as session:
        stored = session.get(Approval, row_id)
        session.delete(stored)
        with pytest.raises(AppendOnlyViolation):
            session.flush()
        session.rollback()

    with factory() as session:
        assert session.get(Approval, row_id).decision == "approved"


def test_approval_ddl_compiles_for_mssql():
    ddl = str(CreateTable(Approval.__table__).compile(dialect=mssql.dialect()))
    assert "CREATE TABLE approval" in ddl
    assert "decision IN ('approved', 'rejected')" in ddl


def test_approval_record_has_no_credential_fields():
    names = {field.name for field in dataclasses.fields(ApprovalRecord)}
    assert names & {"password", "pwd", "secret", "username", "connection_string"} == set()
