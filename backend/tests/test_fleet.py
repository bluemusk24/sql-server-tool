"""Tests for the org-scoped fleet endpoint (#49).

The multi-firm view: which of *this organization's* servers are
failing, in which areas, and are refresh failures among them — with
other organizations' data invisible (spec §6).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.store import DatabaseStore


def _collection():
    """A crafted live collection: a failing refresh pipeline on Sales
    and storage pressure on the same server."""
    return [
        {
            "source_id": "refresh_job_failures",
            "title": "Failed refresh / ETL job steps",
            "category": "refresh",
            "issue_area": "refresh",
            "status": "collected",
            "note": "",
            "evidence": [
                {
                    "job_name": "DW Refresh",
                    "step_name": "Load FactSales",
                    "step_id": 2,
                    "subsystem": "SSIS",
                    "run_status": 0,
                    "message": "Error: The data flow task failed.",
                },
                {
                    "job_name": "DW Refresh",
                    "step_name": "Load FactSales",
                    "step_id": 2,
                    "subsystem": "SSIS",
                    "run_status": 0,
                    "message": "Error: The data flow task failed.",
                },
            ],
            "row_count": 2,
        },
        {
            "source_id": "storage_pressure",
            "title": "Storage free-space percentage",
            "category": "availability",
            "issue_area": "storage",
            "status": "collected",
            "note": "",
            "evidence": [
                {
                    "db_name": "Sales",
                    "file_name": "Sales",
                    "size_mb": 10_000,
                    "used_mb": 9_900,
                }
            ],
            "row_count": 1,
        },
        {
            "source_id": "replication_latency",
            "title": "Replication delivery latency",
            "category": "refresh",
            "issue_area": "refresh",
            "status": "unavailable",
            "note": "query failed: no permission on distribution",
            "evidence": [],
            "row_count": 0,
        },
    ]


def _investigate(client: TestClient, store: DatabaseStore, host: str) -> int:
    inv = store.create_investigation(
        server_host=host,
        server_port=1433,
        auth_type="sql",
        databases=None,
        lookback_hours=8,
    )
    store.set_investigation_status(inv.id, "completed")
    store.generate_report(inv, "Acme Corp", collection=_collection())
    return inv.id


def test_fleet_requires_auth(client: TestClient) -> None:
    assert client.get("/fleet").status_code == 401


def test_fleet_aggregates_failures_per_server(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    session = client.app.state.SessionLocal()
    try:
        store = DatabaseStore(session)
        _investigate(client, store, "sql-a.corp.example")
        _investigate(client, store, "sql-b.corp.example")
        session.commit()
    finally:
        session.close()

    fleet = client.get("/fleet", headers=auth_headers).json()
    assert fleet["organization"] == "Acme Corp"
    hosts = {server["server_host"] for server in fleet["servers"]}
    assert hosts == {
        "sql-a.corp.example",
        "sql-b.corp.example",
        "sql01.acme.example",  # the seeded demo investigation
    }
    # The seeded demo report runs the engine too (seed=1: 9 high,
    # 3 medium, 5 refresh findings), plus one refresh-high + one
    # storage-high per crafted investigation.
    assert fleet["totals"]["servers"] == 3
    assert fleet["totals"]["investigations"] == 3

    server = next(
        s for s in fleet["servers"] if s["server_host"] == "sql-a.corp.example"
    )
    # One repeated SSIS refresh failure (high) + storage at 1% free (high).
    assert server["refresh_findings"] == 1
    assert server["findings_by_severity"]["high"] == 2
    assert server["total_findings"] == 2
    assert server["areas"]["refresh"]["findings"] == 1
    assert server["areas"]["storage"]["severity"] == "high"
    assert "No primary cause" not in server["latest_primary_cause"]
    assert server["inconclusive"] is False  # a gap existed but confidence held

    totals = fleet["totals"]
    assert totals["refresh_findings"] == 7  # 5 seeded + 1 per crafted server
    assert totals["findings"]["high"] == 13  # 9 seeded + 2 per crafted server
    assert totals["findings"]["medium"] == 3  # all from the seeded report

    failures = fleet["recent_failures"]
    assert failures
    assert any(f["area"] == "refresh" for f in failures)
    assert all(f["severity"] in ("high", "medium") for f in failures)
    # Recent failures span the seeded and crafted investigations.
    assert {f["server_host"] for f in failures} >= {"sql-a.corp.example"}


def test_fleet_ranks_the_worst_server_first(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    session = client.app.state.SessionLocal()
    try:
        store = DatabaseStore(session)
        _investigate(client, store, "sql-calm.corp.example")
        _investigate(client, store, "sql-noisy.corp.example")
        _investigate(client, store, "sql-noisy.corp.example")  # twice as bad
        session.commit()
    finally:
        session.close()

    fleet = client.get("/fleet", headers=auth_headers).json()
    hosts = [s["server_host"] for s in fleet["servers"]]
    # The seeded demo report (seed=1) currently carries the most
    # findings; below it the doubled server must outrank the calm one.
    assert hosts.index("sql-noisy.corp.example") < hosts.index(
        "sql-calm.corp.example"
    )
    noisy = fleet["servers"][hosts.index("sql-noisy.corp.example")]
    assert noisy["investigations"] == 2
    assert noisy["findings_by_severity"]["high"] == 4  # (refresh+storage) x2


def test_fleet_never_leaks_across_organizations(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    session = client.app.state.SessionLocal()
    try:
        store = DatabaseStore(session)
        _investigate(client, store, "sql-a.corp.example")
        store.upsert_sso_user(
            "beta_dba", "Beta Corp", "dba@beta.example"
        )
        beta_token = store.issue_token("beta_dba")
        session.commit()
    finally:
        session.close()

    # Acme sees its servers…
    acme = client.get("/fleet", headers=auth_headers).json()
    assert acme["totals"]["servers"] >= 1

    # …Beta Corp sees nothing of Acme's (spec §6).
    beta = client.get(
        "/fleet", headers={"Authorization": f"Bearer {beta_token}"}
    ).json()
    assert beta["organization"] == "Beta Corp"
    assert beta["totals"] == {
        "servers": 0,
        "investigations": 0,
        "findings": {"high": 0, "medium": 0, "low": 0},
        "refresh_findings": 0,
    }
    assert beta["servers"] == []
    assert beta["recent_failures"] == []
