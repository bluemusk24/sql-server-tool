"""Live collection tests (#31).

No SQL Server is reachable from this environment, so the executor is
driven through an injected fake connection that mimics a pyodbc cursor
over DMV rows; the driver itself is stubbed at ``sys.modules`` where the
import path matters. The env-gated test at the bottom runs for real
against an instance when ``SQLDIAG_TEST_SERVER`` (and friends) are set —
that is the hook for when a reachable server exists.
"""

from __future__ import annotations

import json
import os
import sys
import types

import pytest
from fastapi.testclient import TestClient

from app import live
from app.live import (
    STATEMENT_TEXT_LIMIT,
    ConnectionSettings,
    LiveUnavailableError,
    build_connection_string,
    display_target,
    query_timeout_seconds,
    redact_statement_text,
    render_sql,
    run_collection,
)

PASSWORD = "S3cret-Password-!"


def entry(source_id: str, sql: str = "SELECT 1 AS ok") -> dict:
    return {
        "id": source_id,
        "title": f"Title {source_id}",
        "category": "performance",
        "issue_area": "blocking",
        "sql": sql,
    }


class FakeCursor:
    def __init__(self, rows=None, description=None, error=None):
        self.rows = rows or []
        self.description = description if description is not None else []
        self.error = error
        self.executed: list[str] = []

    def execute(self, sql: str) -> None:
        self.executed.append(sql)
        if self.error is not None:
            raise self.error

    def fetchmany(self, size: int):
        return self.rows[:size]


class FakeConnection:
    """Hands out one fake cursor per query (so sources can differ)."""

    def __init__(self, cursors: list[FakeCursor] | None = None):
        self._cursors = cursors or [FakeCursor()]
        self.timeout: int | None = None
        self.closed = False

    def cursor(self) -> FakeCursor:
        if len(self._cursors) > 1:
            return self._cursors.pop(0)
        return self._cursors[0]

    def close(self) -> None:
        self.closed = True


def sql_settings(**overrides) -> ConnectionSettings:
    base = {
        "server_host": "sql-prod-01",
        "server_port": 1433,
        "auth_type": "sql",
        "username": "svc_diag",
        "password": PASSWORD,
    }
    base.update(overrides)
    return ConnectionSettings(**base)


# ── Connection strings & secrecy ─────────────────────────────────────
def test_connection_string_is_read_only_and_credential_free() -> None:
    conn_str = build_connection_string(sql_settings())
    assert "DRIVER={ODBC Driver 18 for SQL Server}" in conn_str
    assert "SERVER=sql-prod-01,1433" in conn_str
    assert "ApplicationIntent=ReadOnly" in conn_str
    assert "Encrypt=yes" in conn_str
    # Credentials are passed as kwargs, never embedded in the string.
    assert PASSWORD not in conn_str
    assert "svc_diag" not in conn_str


def test_connection_string_for_ad_uses_integrated_auth() -> None:
    conn_str = build_connection_string(sql_settings(auth_type="ad"))
    assert "Authentication=ActiveDirectoryIntegrated" in conn_str
    assert "UID" not in conn_str and "PWD" not in conn_str


def test_connection_string_for_named_instance_omits_the_port() -> None:
    # A named instance addresses itself: the driver resolves the port via
    # SQL Browser, so nothing numeric may be appended (#37).
    settings = sql_settings(server_host="ATADBETSP001\\OISSQL")
    conn_str = build_connection_string(settings)
    assert "SERVER=ATADBETSP001\\OISSQL;" in conn_str
    assert ",1433" not in conn_str


def test_display_target_shapes_error_messages() -> None:
    # Plain hosts keep `host:port`; named instances never show a port (#37).
    assert display_target(sql_settings()) == "sql-prod-01:1433"
    assert (
        display_target(sql_settings(server_host="ATADBETSP001\\OISSQL"))
        == "ATADBETSP001\\OISSQL"
    )


def test_settings_repr_never_contains_the_password() -> None:
    assert PASSWORD not in repr(sql_settings())
    assert PASSWORD not in str(sql_settings())


def test_query_timeout_reads_the_env(monkeypatch) -> None:
    assert query_timeout_seconds() == 30  # default when unset/garbage
    monkeypatch.setenv("LIVE_QUERY_TIMEOUT_SECONDS", "not-a-number")
    assert query_timeout_seconds() == 30
    monkeypatch.setenv("LIVE_QUERY_TIMEOUT_SECONDS", "0")
    assert query_timeout_seconds() == 30
    monkeypatch.setenv("LIVE_QUERY_TIMEOUT_SECONDS", "45")
    assert query_timeout_seconds() == 45


# ── SQL rendering (parameter binding) ────────────────────────────────
def test_render_sql_binds_lookback_and_databases() -> None:
    sql = render_sql(
        "SELECT * FROM t WHERE hours < :lookback_hours AND db IN (:database_list)",
        8,
        ["Orders", "Ledger"],
    )
    assert "hours < 8" in sql
    assert "[Orders], [Ledger]" in sql
    assert ":lookback_hours" not in sql


def test_render_sql_refuses_a_hostile_database_name() -> None:
    with pytest.raises(LiveUnavailableError):
        render_sql(
            "SELECT * FROM t WHERE db IN (:database_list)",
            4,
            ["db1']; DROP TABLE users;--"],
        )


def test_render_sql_refuses_an_empty_scope_for_a_database_query() -> None:
    with pytest.raises(LiveUnavailableError):
        render_sql("SELECT * FROM t WHERE db IN (:database_list)", 4, [])


# ── Collection with a fake server ────────────────────────────────────
def test_collects_rows_as_json_evidence_and_times_the_session() -> None:
    cursor = FakeCursor(
        rows=[(71, "LCK_M_X", 5200)],
        description=[("blocking_session_id",), ("wait_type",), ("wait_ms",)],
    )
    conn = FakeConnection([cursor])
    outcomes = run_collection(
        [entry("blocking")],
        sql_settings(timeout_seconds=45),
        lookback_hours=8,
        connect_fn=lambda settings: conn,
    )

    (outcome,) = outcomes
    assert outcome.status == "collected"
    assert outcome.evidence == [
        {
            "blocking_session_id": 71,
            "wait_type": "LCK_M_X",
            "wait_ms": 5200,
        }
    ]
    assert outcome.row_count == 1
    assert cursor.executed == ["SELECT 1 AS ok"]
    assert conn.timeout == 45  # per-query timeout applied to the session
    assert conn.closed


def test_a_failed_query_becomes_a_gap_and_the_others_still_run() -> None:
    good = FakeCursor(rows=[(1,)], description=[("n",)])
    bad = FakeCursor(error=Exception("The user does not have permission to run this"))
    conn = FakeConnection([bad, good])

    outcomes = run_collection(
        [entry("agent_jobs"), entry("blocking")],
        sql_settings(),
        lookback_hours=4,
        connect_fn=lambda settings: conn,
    )

    failed, collected = outcomes
    assert failed.status == "unavailable"
    assert "permission" in failed.note
    assert failed.evidence == []  # nothing fabricated for the failed source
    assert collected.status == "collected"


def test_a_connection_failure_marks_everything_unavailable() -> None:
    def refuse(settings):
        raise LiveUnavailableError("could not connect to sql-prod-01,1433: timeout")

    outcomes = run_collection(
        [entry("a"), entry("b")],
        sql_settings(),
        lookback_hours=1,
        connect_fn=refuse,
    )
    assert [o.status for o in outcomes] == ["unavailable", "unavailable"]
    assert all("could not connect" in o.note for o in outcomes)


def test_evidence_is_capped_per_source() -> None:
    rows = [(i,) for i in range(live.MAX_EVIDENCE_ROWS + 25)]
    conn = FakeConnection([FakeCursor(rows=rows, description=[("n",)])])
    (outcome,) = run_collection(
        [entry("big")],
        sql_settings(),
        lookback_hours=1,
        connect_fn=lambda settings: conn,
    )
    assert outcome.row_count == live.MAX_EVIDENCE_ROWS
    assert "first" in outcome.note


# ── Driver & credential handling ─────────────────────────────────────
def test_missing_driver_is_reported_cleanly(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "pyodbc", None)  # `import pyodbc` fails
    with pytest.raises(LiveUnavailableError, match="not installed"):
        live.open_connection(sql_settings())


def test_sql_auth_without_credentials_is_refused(monkeypatch) -> None:
    stub = types.SimpleNamespace(connect=lambda *a, **k: None)
    monkeypatch.setitem(sys.modules, "pyodbc", stub)
    with pytest.raises(LiveUnavailableError, match="username and password"):
        live.open_connection(sql_settings(username=None, password=None))


def test_credentials_reach_the_driver_as_kwargs_not_the_string(
    monkeypatch,
) -> None:
    seen: dict = {}

    def connect(conn_str, **kwargs):
        seen["conn_str"] = conn_str
        seen["kwargs"] = kwargs
        return object()

    monkeypatch.setitem(sys.modules, "pyodbc", types.SimpleNamespace(connect=connect))
    live.open_connection(sql_settings())
    assert seen["kwargs"]["uid"] == "svc_diag"
    assert seen["kwargs"]["pwd"] == PASSWORD
    assert PASSWORD not in seen["conn_str"]


# ── API-level: live run + credential secrecy ─────────────────────────
def _approve_all(client: TestClient, headers: dict, inv_id: int) -> None:
    catalog = client.get(f"/investigations/{inv_id}", headers=headers).json()["catalog"]
    decisions = {entry["id"]: "approved" for entry in catalog}
    response = client.post(
        f"/investigations/{inv_id}/approvals",
        json={"decisions": decisions},
        headers=headers,
    )
    assert response.status_code == 200, response.text


def test_live_run_produces_real_statuses_and_never_stores_the_password(
    client: TestClient, auth_headers: dict, monkeypatch
) -> None:
    cursor = FakeCursor(
        rows=[(71, "LCK_M_X")],
        description=[("blocking_session_id",), ("wait_type",)],
    )
    monkeypatch.setattr(
        live, "open_connection", lambda settings: FakeConnection([cursor])
    )

    created = client.post(
        "/investigations",
        json={
            "server_host": "sql-prod-01",
            "server_port": 1433,
            "auth_type": "sql",
            "lookback_hours": 8,
        },
        headers=auth_headers,
    ).json()
    _approve_all(client, auth_headers, created["id"])

    started = client.post(
        f"/investigations/{created['id']}/run",
        json={"credentials": {"username": "svc_diag", "password": PASSWORD}},
        headers=auth_headers,
    )
    assert started.status_code == 202, started.text

    report = client.get(f"/reports/{created['id']}", headers=auth_headers).json()
    statuses = {source["source_id"]: source["status"] for source in report["data_sources"]}
    assert set(statuses.values()) == {"collected"}
    evidence = report["findings_by_area"][0]["evidence"]
    assert evidence[0]["details"] == {"blocking_session_id": 71, "wait_type": "LCK_M_X"}

    # The password must not survive the request anywhere: not in the report,
    # not in the job, not in any investigation or approval payload.
    job_id = started.json()["job_id"]
    job = client.get(f"/jobs/{job_id}", headers=auth_headers).json()
    investigation = client.get(
        f"/investigations/{created['id']}", headers=auth_headers
    ).json()
    approvals = client.get(
        f"/investigations/{created['id']}/approvals", headers=auth_headers
    ).json()
    for payload in (report, job, investigation, approvals):
        assert PASSWORD not in json.dumps(payload)


def test_live_run_without_a_driver_marks_gaps_honestly(
    client: TestClient, auth_headers: dict, monkeypatch
) -> None:
    def refuse(settings):
        raise LiveUnavailableError("pyodbc / the ODBC Driver 18 is not installed")

    monkeypatch.setattr(live, "open_connection", refuse)

    created = client.post(
        "/investigations",
        json={
            "server_host": "sql-prod-01",
            "auth_type": "sql",
            "lookback_hours": 4,
        },
        headers=auth_headers,
    ).json()
    _approve_all(client, auth_headers, created["id"])

    started = client.post(
        f"/investigations/{created['id']}/run",
        json={"credentials": {"username": "svc_diag", "password": PASSWORD}},
        headers=auth_headers,
    )
    assert started.status_code == 202, started.text

    report = client.get(f"/reports/{created['id']}", headers=auth_headers).json()
    assert all(source["status"] == "unavailable" for source in report["data_sources"])
    assert report["coverage_gaps"]  # every source is an explicit gap
    assert report["inconclusive"] is True
    assert report["findings_by_area"] == []  # nothing fabricated


def test_sql_auth_without_a_username_is_rejected_before_any_connect(
    client: TestClient, auth_headers: dict
) -> None:
    created = client.post(
        "/investigations",
        json={"server_host": "sql-prod-01", "auth_type": "sql", "lookback_hours": 1},
        headers=auth_headers,
    ).json()
    _approve_all(client, auth_headers, created["id"])

    response = client.post(
        f"/investigations/{created['id']}/run",
        json={"credentials": {"password": PASSWORD}},
        headers=auth_headers,
    )
    assert response.status_code == 422
    assert "username and password" in response.json()["detail"]


def test_run_without_credentials_produces_the_synthetic_report(
    client: TestClient, auth_headers: dict
) -> None:
    created = client.post(
        "/investigations",
        json={"server_host": "sql-prod-01", "auth_type": "sql", "lookback_hours": 1},
        headers=auth_headers,
    ).json()
    _approve_all(client, auth_headers, created["id"])

    response = client.post(
        f"/investigations/{created['id']}/run", headers=auth_headers
    )
    assert response.status_code == 202, response.text
    report = client.get(f"/reports/{created['id']}", headers=auth_headers).json()
    # The no-credentials path runs the labeled synthetic pipeline (#26).
    assert report["synthetic"] is True
    assert "SYNTHETIC" in report["rationale"]


# ── Real-server integration (skipped until a server exists) ──────────
@pytest.mark.skipif(
    not os.environ.get("SQLDIAG_TEST_SERVER"),
    reason="set SQLDIAG_TEST_SERVER (+ _PORT/_USER/_PASSWORD) to test a live instance",
)
def test_real_server_connect_and_collect() -> None:
    settings = ConnectionSettings(
        server_host=os.environ["SQLDIAG_TEST_SERVER"],
        server_port=int(os.environ.get("SQLDIAG_TEST_PORT", "1433")),
        auth_type=os.environ.get("SQLDIAG_TEST_AUTH", "sql"),
        username=os.environ.get("SQLDIAG_TEST_USER"),
        password=os.environ.get("SQLDIAG_TEST_PASSWORD"),
    )
    from app.store import CATALOG

    outcomes = run_collection(
        [
            {
                "id": row["id"],
                "title": row["title"],
                "category": row["category"],
                "issue_area": row["issue_area"],
                "sql": row["sql"],
            }
            for row in CATALOG
        ],
        settings,
        lookback_hours=8,
    )
    assert outcomes
    assert all(outcome.status in ("collected", "partial", "unavailable")
               for outcome in outcomes)


# ── Statement-text redaction (#52) ───────────────────────────────────


def test_redact_statement_text_keeps_short_and_non_string_values() -> None:
    assert redact_statement_text("SELECT 1") == "SELECT 1"
    assert (
        redact_statement_text("x" * STATEMENT_TEXT_LIMIT)
        == "x" * STATEMENT_TEXT_LIMIT
    )
    assert redact_statement_text(None) is None
    assert redact_statement_text(42) == 42


def test_redact_statement_text_truncates_with_a_length_marker() -> None:
    long_sql = (
        "SELECT ssn, card FROM customers WHERE region = 'EU'; -- " + "x" * 300
    )
    redacted = redact_statement_text(long_sql)

    # The shape prefix survives (the DBA can still identify the query)…
    assert redacted.startswith(long_sql[:STATEMENT_TEXT_LIMIT])
    # …the cut is visible, naming the original length…
    assert f"[truncated: {len(long_sql)} chars total]" in redacted
    # …and the literals tail is gone.
    assert "x" * 130 not in redacted


def test_collection_truncates_statement_text_at_the_boundary() -> None:
    long_text = (
        "SELECT CustomerId, CardNumber FROM dbo.Customers "
        "WHERE Card = '4111-1111-1111-1111'; " + "z" * 300
    )
    cursor = FakeCursor(
        rows=[(3, long_text, "INSERT ok")],
        description=[("execution_count",), ("text",), ("note",)],
    )
    conn = FakeConnection([cursor])
    outcomes = run_collection(
        [entry("top_queries")],
        sql_settings(),
        lookback_hours=4,
        connect_fn=lambda settings: conn,
    )

    (outcome,) = outcomes
    row = outcome.evidence[0]
    # Statement text: prefix + marker; every other column untouched.
    assert row["text"].startswith(long_text[:STATEMENT_TEXT_LIMIT])
    assert "chars total" in row["text"]
    assert row["execution_count"] == 3
    assert row["note"] == "INSERT ok"


def test_collection_passes_short_statement_text_through_unchanged() -> None:
    short = "SELECT 1 AS ok;"
    cursor = FakeCursor(rows=[(short,)], description=[("text",)])
    conn = FakeConnection([cursor])
    (outcome,) = run_collection(
        [entry("top_queries")],
        sql_settings(),
        lookback_hours=4,
        connect_fn=lambda settings: conn,
    )
    assert outcome.evidence[0]["text"] == short
