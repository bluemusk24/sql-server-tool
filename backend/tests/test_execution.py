"""Unit tests for the diagnostic execution engine (#12).

The engine is driven with a scripted fake connector that returns :class:`Row`
objects and raises the categorized #6 errors, so the whole suite runs with no
SQL Server. The live-server path lives in the gated ``tests/test_execution_db.py``.

Covered: all-success capture (rows/count/duration), isolation (one error does
not abort the run), timeout handling, approval gating, unreachable-server
inventory, the not-applicable predicate, parameter binding, truncation, and
that no credential material reaches the evidence.
"""

from __future__ import annotations

import pytest

from app.credentials import CredentialStore, SqlCredentials
from app.diagnostics.catalog import Catalog, DiagnosticQuery, build_catalog
from app.evidence.models import QueryStatus, SourceStatus
from app.execution import MAX_EVIDENCE_ROWS, RunNotApproved, run_diagnostic
from app.investigations.repository import (
    InMemoryApprovalRepository,
    InMemoryInvestigationRepository,
)
from app.sql.connector import (
    ConnectionRefused,
    QueryError,
    Row,
)
from app.sql.connector import (
    PermissionError as SqlPermissionError,
)

SECRET = "s3cret-P@ssw0rd"
USERNAME = "diag_dba"


def _query(**overrides) -> DiagnosticQuery:
    values = {
        "id": "ok_query",
        "title": "OK query",
        "description": "A query that succeeds.",
        "category": "blocking",
        "issue_area": "blocking",
        "sql": "SELECT ok FROM sys.x",
        "params": (),
        "requires_approval": True,
        "expected_impact": "Minimal.",
        "versions": ("2008+",),
        "min_permission": "VIEW SERVER STATE",
        "read_only": True,
    }
    values.update(overrides)
    return DiagnosticQuery(**values)


def _catalog(*queries: DiagnosticQuery) -> Catalog:
    catalog = Catalog()
    for query in queries:
        catalog.register(query)
    return catalog


class FakeConnection:
    def __init__(self, database: str) -> None:
        self.database = database
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeConnector:
    """Scripted connector: canned rows per SQL, canned errors per SQL/database."""

    def __init__(
        self,
        *,
        connect_error: Exception | None = None,
        connect_errors_by_db: dict[str, Exception] | None = None,
        rows_by_sql: dict[str, list[Row]] | None = None,
        errors_by_sql: dict[str, Exception] | None = None,
    ) -> None:
        self.connect_error = connect_error
        self.connect_errors_by_db = connect_errors_by_db or {}
        self.rows_by_sql = rows_by_sql or {}
        self.errors_by_sql = errors_by_sql or {}
        self.connect_calls: list[dict] = []
        self.query_calls: list[dict] = []
        self.connections: list[FakeConnection] = []

    def connect(self, host, database, username, password, *, port=1433, login_timeout=15, query_timeout=30, read_only=True):
        self.connect_calls.append(
            {
                "host": host,
                "database": database,
                "username": username,
                "password": password,
                "port": port,
                "login_timeout": login_timeout,
                "query_timeout": query_timeout,
                "read_only": read_only,
            }
        )
        if database in self.connect_errors_by_db:
            raise self.connect_errors_by_db[database]
        if self.connect_error is not None:
            raise self.connect_error
        conn = FakeConnection(database)
        self.connections.append(conn)
        return conn

    def query(self, conn, sql, *, params=None, allow_write=False, timeout=None):
        self.query_calls.append(
            {
                "sql": sql,
                "params": params,
                "allow_write": allow_write,
                "timeout": timeout,
                "database": conn.database,
            }
        )
        if sql in self.errors_by_sql:
            raise self.errors_by_sql[sql]
        return list(self.rows_by_sql.get(sql, []))


def _row(**values) -> Row:
    return Row(tuple(values), tuple(values.values()))


def _setup(
    catalog: Catalog,
    *,
    connector: FakeConnector | None = None,
    approve: bool = True,
    scope_kind: str = "instance",
    databases: list[str] | None = None,
    lookback_hours: int = 4,
    store_credentials: bool = True,
):
    repository = InMemoryInvestigationRepository()
    investigation = repository.create_investigation(
        organization_id=1,
        server_host="sql01",
        server_port=1433,
        auth_type="sql",
        scope_kind=scope_kind,
        databases=databases,
        lookback_hours=lookback_hours,
        status="draft",
    )
    approvals = InMemoryApprovalRepository()
    if approve:
        for query in catalog.all():
            if query.requires_approval:
                approvals.append_decision(
                    investigation_id=investigation.id,
                    catalog_id=query.id,
                    decision="approved",
                    actor="dba",
                    note=None,
                )
    store = CredentialStore()
    if store_credentials:
        store.store(str(investigation.id), SqlCredentials(USERNAME, SECRET))
    connector = connector or FakeConnector()
    result = run_diagnostic(
        investigation.id,
        investigation_repository=repository,
        approval_repository=approvals,
        catalog=catalog,
        credential_store=store,
        connector=connector,
    )
    return result, connector, repository, investigation


# --- all-success ------------------------------------------------------------


def test_all_success_captures_rows_count_and_duration_per_source():
    catalog = _catalog(_query(id="ok_query", sql="SELECT a"), _query(id="ok_query_2", sql="SELECT b"))
    connector = FakeConnector(
        rows_by_sql={"SELECT a": [_row(id=1), _row(id=2)], "SELECT b": [_row(id=3)]}
    )

    result, connector, _repo, investigation = _setup(catalog, connector=connector)

    assert result.investigation_id == investigation.id
    assert result.status == "succeeded"
    assert [source.status for source in result.sources] == [
        SourceStatus.collected,
        SourceStatus.collected,
    ]
    counts = {evidence.query_id: evidence for evidence in result.evidence}
    assert counts["ok_query"].row_count == 2
    assert counts["ok_query_2"].row_count == 1
    assert counts["ok_query"].rows == [{"id": 1}, {"id": 2}]
    assert counts["ok_query"].status is QueryStatus.ok
    assert counts["ok_query"].error is None
    assert counts["ok_query"].duration_ms >= 0.0
    assert all(evidence.truncated is False for evidence in result.evidence)


def test_non_approval_required_query_runs_without_a_decision():
    catalog = _catalog(
        _query(id="free_query", sql="SELECT f", requires_approval=False)
    )
    result, connector, _repo, _inv = _setup(catalog, approve=False)

    assert len(connector.query_calls) == 1
    assert [source.status for source in result.sources] == [SourceStatus.collected]


# --- isolation / partial failure -------------------------------------------


def test_partial_failure_is_isolated_and_marks_exactly_failed_sources():
    catalog = _catalog(
        _query(id="ok_query", sql="SELECT a"),
        _query(id="denied_query", sql="SELECT b"),
        _query(id="broken_query", sql="SELECT c"),
    )
    connector = FakeConnector(
        rows_by_sql={"SELECT a": [_row(id=1)]},
        errors_by_sql={
            "SELECT b": SqlPermissionError("SELECT permission was denied"),
            "SELECT c": QueryError("Invalid object name 'dbonope'"),
        },
    )

    result, connector, _repo, _inv = _setup(catalog, connector=connector)

    assert result.status == "succeeded"
    statuses = {source.query_id: source.status for source in result.sources}
    assert statuses == {
        "ok_query": SourceStatus.collected,
        "denied_query": SourceStatus.unavailable_permission,
        "broken_query": SourceStatus.unavailable_setting,
    }
    # Isolation: every query was still attempted, in catalog order.
    assert [call["sql"] for call in connector.query_calls] == [
        "SELECT a",
        "SELECT b",
        "SELECT c",
    ]
    errors = {e.query_id: e for e in result.evidence}
    assert errors["denied_query"].status is QueryStatus.error
    assert "permission was denied" in errors["denied_query"].error
    assert errors["broken_query"].status is QueryStatus.error


def test_timeout_is_recorded_as_timeout_status():
    catalog = _catalog(_query(id="slow_query", sql="SELECT slow"))
    connector = FakeConnector(
        errors_by_sql={"SELECT slow": QueryError("Query timed out: HYT00")}
    )

    result, _connector, _repo, _inv = _setup(catalog, connector=connector)

    evidence = result.evidence[0]
    assert evidence.status is QueryStatus.timeout
    assert result.sources[0].status is SourceStatus.unavailable_setting


def test_engine_never_marks_a_failed_source_collected():
    catalog = _catalog(_query(id="denied_query", sql="SELECT b"))
    connector = FakeConnector(
        errors_by_sql={"SELECT b": SqlPermissionError("denied")}
    )
    result, _c, _r, _i = _setup(catalog, connector=connector)

    assert SourceStatus.collected not in [s.status for s in result.sources]


# --- approval gating --------------------------------------------------------


def test_run_aborts_when_not_approved_and_never_connects():
    catalog = _catalog(_query(id="ok_query", sql="SELECT a"))
    connector = FakeConnector()

    with pytest.raises(RunNotApproved) as excinfo:
        _setup(catalog, connector=connector, approve=False)

    assert "approved" in str(excinfo.value)
    assert connector.connect_calls == []
    assert connector.query_calls == []


def test_run_aborts_for_unknown_investigation():
    catalog = _catalog(_query(id="ok_query", sql="SELECT a"))
    repository = InMemoryInvestigationRepository()
    approvals = InMemoryApprovalRepository()
    store = CredentialStore()

    with pytest.raises(RunNotApproved):
        run_diagnostic(
            999,
            investigation_repository=repository,
            approval_repository=approvals,
            catalog=catalog,
            credential_store=store,
            connector=FakeConnector(),
        )


# --- unreachable server -----------------------------------------------------


def test_unreachable_server_marks_every_source_unavailable_connection():
    catalog = _catalog(_query(id="a", sql="SELECT a"), _query(id="b", sql="SELECT b"))
    connector = FakeConnector(
        connect_error=ConnectionRefused("Could not connect to the SQL Server")
    )

    result, _connector, _repo, _inv = _setup(catalog, connector=connector)

    assert result.status == "succeeded"
    assert [source.status for source in result.sources] == [
        SourceStatus.unavailable_connection,
        SourceStatus.unavailable_connection,
    ]
    assert all(evidence.error for evidence in result.evidence)
    assert connector.query_calls == []


def test_denied_database_is_unavailable_permission_not_connection():
    catalog = _catalog(_query(id="a", sql="SELECT a"))
    connector = FakeConnector(
        connect_error=SqlPermissionError("Login denied for the requested database")
    )
    result, _c, _r, _i = _setup(catalog, connector=connector)

    assert result.sources[0].status is SourceStatus.unavailable_permission


def test_multi_database_collected_is_not_downgraded_by_one_failure():
    catalog = _catalog(_query(id="a", sql="SELECT a"))
    connector = FakeConnector(
        connect_errors_by_db={"bad_db": ConnectionRefused("nope")},
        rows_by_sql={"SELECT a": [_row(id=1)]},
    )
    result, _c, _r, _i = _setup(
        catalog,
        connector=connector,
        scope_kind="databases",
        databases=["bad_db", "good_db"],
    )

    assert result.sources[0].status is SourceStatus.collected
    assert {e.database for e in result.evidence} == {"bad_db", "good_db"}


# --- applicability ----------------------------------------------------------


def test_empty_retained_deadlock_evidence_is_not_applicable():
    catalog = _catalog(
        _query(
            id="deadlock_system_health",
            title="Deadlocks",
            category="blocking",
            issue_area="deadlocks",
            sql="SELECT ring FROM sys.dm_os_ring_buffers",
        )
    )
    connector = FakeConnector(rows_by_sql={"SELECT ring FROM sys.dm_os_ring_buffers": []})

    result, _c, _r, _i = _setup(catalog, connector=connector)

    assert result.sources[0].status is SourceStatus.not_applicable
    assert SourceStatus.collected not in [s.status for s in result.sources]
    assert "not applicable" in result.sources[0].note.lower()


def test_retained_deadlock_evidence_is_collected():
    catalog = _catalog(
        _query(
            id="deadlock_system_health",
            category="blocking",
            issue_area="deadlocks",
            sql="SELECT ring FROM sys.dm_os_ring_buffers",
        )
    )
    connector = FakeConnector(
        rows_by_sql={"SELECT ring FROM sys.dm_os_ring_buffers": [_row(ring="graph")]}
    )
    result, _c, _r, _i = _setup(catalog, connector=connector)

    assert result.sources[0].status is SourceStatus.collected


# --- credentials ------------------------------------------------------------


def test_no_credential_material_reaches_evidence_or_result():
    catalog = _catalog(_query(id="ok_query", sql="SELECT a"))
    connector = FakeConnector(
        errors_by_sql={
            "SELECT a": QueryError(f"bad login for user {USERNAME} password {SECRET}")
        }
    )
    result, _c, _r, _i = _setup(catalog, connector=connector)

    rendered = str(result.model_dump())
    assert SECRET not in rendered
    assert USERNAME not in rendered
    assert result.evidence[0].error is not None
    assert "****" in result.evidence[0].error


# --- connector contract / parameters ----------------------------------------


def test_connector_is_invoked_read_only_and_with_query_timeout():
    catalog = _catalog(_query(id="ok_query", sql="SELECT a"))
    connector = FakeConnector(rows_by_sql={"SELECT a": [_row(id=1)]})
    _result, connector, _repo, _inv = _setup(catalog, connector=connector)

    assert connector.connect_calls[0]["read_only"] is True
    assert connector.connect_calls[0]["query_timeout"] == 30
    assert connector.query_calls[0]["allow_write"] is False
    assert connector.query_calls[0]["timeout"] == 30


def test_catalog_parameters_are_bound_in_order():
    catalog = _catalog(
        _query(
            id="param_query",
            sql="SELECT x WHERE t >= ?",
            params=("lookback_hours",),
        )
    )
    connector = FakeConnector(
        rows_by_sql={"SELECT x WHERE t >= ?": [_row(x=1)]}
    )
    result, connector, _repo, _inv = _setup(
        catalog, connector=connector, lookback_hours=8
    )

    assert connector.query_calls[0]["params"] == (8,)
    assert result.sources[0].status is SourceStatus.collected


def test_unknown_parameter_is_recorded_not_fatal():
    catalog = _catalog(
        _query(id="param_query", sql="SELECT x WHERE t = ?", params=("mystery",))
    )
    result, _c, _r, _i = _setup(catalog)

    assert result.status == "succeeded"
    assert result.sources[0].status is SourceStatus.unavailable_setting
    assert "mystery" in result.evidence[0].error


# --- truncation -------------------------------------------------------------


def test_large_result_is_recorded_but_truncated(monkeypatch):
    monkeypatch.setattr("app.execution.MAX_EVIDENCE_ROWS", 2)
    catalog = _catalog(_query(id="ok_query", sql="SELECT a"))
    connector = FakeConnector(
        rows_by_sql={"SELECT a": [_row(id=1), _row(id=2), _row(id=3)]}
    )
    result, _c, _r, _i = _setup(catalog, connector=connector)

    evidence = result.evidence[0]
    assert evidence.truncated is True
    assert evidence.row_count == 3
    assert len(evidence.rows) == 2
    assert MAX_EVIDENCE_ROWS == 100_000


# --- catalog composition ----------------------------------------------------


def test_engine_runs_the_shipped_catalog_and_completes_on_connection_failure():
    catalog = build_catalog()
    connector = FakeConnector(connect_error=ConnectionRefused("no server"))
    result, _c, _r, _i = _setup(catalog, connector=connector)

    assert len(result.sources) == len(catalog.all())
    assert all(
        source.status is SourceStatus.unavailable_connection
        for source in result.sources
    )
