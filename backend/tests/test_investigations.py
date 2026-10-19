"""Tests for investigations: create/list/get, approvals, and run gating."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.store import CATALOG, DatabaseStore


def _create(client: TestClient, headers: dict[str, str]) -> dict:
    response = client.post(
        "/investigations",
        headers=headers,
        json={
            "server_host": "sql02.acme.example",
            "server_port": 1433,
            "auth_type": "sql",
            "databases": None,
            "lookback_hours": 8,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_list_requires_auth(client: TestClient) -> None:
    assert client.get("/investigations").status_code == 401


def test_create_and_get(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = _create(client, auth_headers)
    assert created["status"] == "draft"
    assert len(created["catalog"]) == len(CATALOG)

    # The target server is part of the payload, not just the report (#34).
    assert created["server_host"] == "sql02.acme.example"
    assert created["server_port"] == 1433
    assert created["auth_type"] == "sql"
    assert created["databases"] is None
    assert created["lookback_hours"] == 8
    assert created["created_at"]

    response = client.get(f"/investigations/{created['id']}", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == created["id"]
    assert body["server_host"] == "sql02.acme.example"
    assert body["server_port"] == 1433


def test_create_accepts_a_named_instance(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    # The exact input a DBA types in SSMS (#37).
    response = client.post(
        "/investigations",
        headers=auth_headers,
        json={
            "server_host": "ATADBETSP001\\OISSQL",
            "server_port": 1433,
            "auth_type": "sql",
            "databases": None,
            "lookback_hours": 4,
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["server_host"] == "ATADBETSP001\\OISSQL"


def test_create_with_a_database_scope(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """A scope of specific databases is a normal request, not an error.

    It used to raise inside the store (a list was treated as a string),
    which failed the creation and fired the "Repeated creation failures"
    alert.
    """
    response = client.post(
        "/investigations",
        headers=auth_headers,
        json={
            "server_host": "sql02.acme.example",
            "server_port": 1433,
            "auth_type": "sql",
            "databases": ["Sales", "Orders"],
            "lookback_hours": 8,
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["databases"] == ["Sales", "Orders"]

    # The scope survives the round trip through the store.
    listed = client.get("/investigations", headers=auth_headers).json()
    target = next(row for row in listed if row["id"] == response.json()["id"])
    assert target["databases"] == ["Sales", "Orders"]


def test_create_normalizes_a_comma_joined_database_scope(
    client: TestClient,
) -> None:
    """Older clients sent the scope as one comma-joined string; the store
    still accepts it and stores a clean list of names."""
    session = client.app.state.SessionLocal()
    try:
        store = DatabaseStore(session)
        inv = store.create_investigation(
            server_host="sql02.acme.example",
            server_port=1433,
            auth_type="sql",
            databases="Sales, Orders , ",
            lookback_hours=8,
        )
        session.commit()
    finally:
        session.close()
    assert inv.databases == ["Sales", "Orders"]


def test_create_rejects_malformed_hosts(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    # Server-side guard: the frontend's check alone can be bypassed (#37).
    for bad in ["HOST\\", "\\INST", "A\\B\\C", "host\\bad name", "not a host!"]:
        response = client.post(
            "/investigations",
            headers=auth_headers,
            json={
                "server_host": bad,
                "server_port": 1433,
                "auth_type": "sql",
                "databases": None,
                "lookback_hours": 4,
            },
        )
        assert response.status_code == 422, (bad, response.text)


def test_list_includes_seeded_and_created(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    created = _create(client, auth_headers)
    response = client.get("/investigations", headers=auth_headers)
    assert response.status_code == 200
    rows = response.json()
    ids = [row["id"] for row in rows]
    assert created["id"] in ids

    # Each list row shows which SQL Server it targets (#34).
    target = next(row for row in rows if row["id"] == created["id"])
    assert target["server_host"] == "sql02.acme.example"
    assert target["server_port"] == 1433
    assert target["auth_type"] == "sql"
    assert target["lookback_hours"] == 8
    assert target["created_at"]


def test_backend_log_names_target_server(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """The backend's own log names the investigated server on create and
    on run start, so an observer can watch without the frontend (#34).

    A handler is attached to the logger directly (rather than relying on
    pytest's caplog capture plumbing, which proved flaky under CI): this
    is exactly what an external log shipper would do.
    """

    class _Recorder(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.messages: list[str] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.messages.append(record.getMessage())

    recorder = _Recorder()
    target_logger = logging.getLogger("sqldiag.investigations")
    target_logger.addHandler(recorder)
    previous_level = target_logger.level
    target_logger.setLevel(logging.INFO)
    try:
        created = _create(client, auth_headers)
        inv_id = created["id"]

        decisions = {entry.id: "approved" for entry in CATALOG}
        client.post(
            f"/investigations/{inv_id}/approvals",
            headers=auth_headers,
            json={"decisions": decisions},
        )
        response = client.post(f"/investigations/{inv_id}/run", headers=auth_headers)
        assert response.status_code == 202
    finally:
        target_logger.removeHandler(recorder)
        target_logger.setLevel(previous_level)

    messages = recorder.messages
    created_line = next(m for m in messages if m.startswith("investigation"))
    assert "sql02.acme.example:1433" in created_line
    assert "dba" in created_line

    run_line = next(m for m in messages if m.startswith("run started"))
    assert f"investigation {inv_id}" in run_line
    assert "sql02.acme.example:1433" in run_line


def test_get_unknown_404(client: TestClient, auth_headers: dict[str, str]) -> None:
    assert (
        client.get("/investigations/9999", headers=auth_headers).status_code == 404
    )


def test_create_validates_lookback(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post(
        "/investigations",
        headers=auth_headers,
        json={
            "server_host": "h",
            "server_port": 1433,
            "auth_type": "sql",
            "lookback_hours": 5,  # not in {1,4,8,24}
        },
    )
    assert response.status_code == 422


def test_approvals_flow_to_approved(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    created = _create(client, auth_headers)
    inv_id = created["id"]

    # Initially pending with no decisions.
    response = client.get(f"/investigations/{inv_id}/approvals", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["state"] == "pending"

    # Approve every catalog entry.
    decisions = {entry.id: "approved" for entry in CATALOG}
    response = client.post(
        f"/investigations/{inv_id}/approvals",
        headers=auth_headers,
        json={"decisions": decisions, "note": "ok"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "approved"
    assert all(q["decision"] == "approved" for q in body["queries"])


def test_approval_reject_wins(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = _create(client, auth_headers)
    inv_id = created["id"]
    decisions = {entry.id: "approved" for entry in CATALOG}
    decisions[CATALOG[0].id] = "rejected"
    response = client.post(
        f"/investigations/{inv_id}/approvals",
        headers=auth_headers,
        json={"decisions": decisions},
    )
    assert response.json()["state"] == "rejected"


def test_approvals_unknown_catalog_422(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    created = _create(client, auth_headers)
    inv_id = created["id"]
    response = client.post(
        f"/investigations/{inv_id}/approvals",
        headers=auth_headers,
        json={"decisions": {"not_a_real_id": "approved"}},
    )
    assert response.status_code == 422


def test_run_blocked_before_approval(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    created = _create(client, auth_headers)
    response = client.post(
        f"/investigations/{created['id']}/run", headers=auth_headers
    )
    assert response.status_code == 409


def test_run_accepted_after_approval(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    created = _create(client, auth_headers)
    inv_id = created["id"]
    decisions = {entry.id: "approved" for entry in CATALOG}
    client.post(
        f"/investigations/{inv_id}/approvals",
        headers=auth_headers,
        json={"decisions": decisions},
    )
    response = client.post(f"/investigations/{inv_id}/run", headers=auth_headers)
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    # The job is queryable and succeeded.
    status = client.get(f"/jobs/{job_id}", headers=auth_headers).json()
    assert status["status"] == "succeeded"
    assert status["investigation_id"] == inv_id

    # A report is available for the run investigation.
    report = client.get(f"/reports/{inv_id}", headers=auth_headers)
    assert report.status_code == 200
    assert report.json()["investigation_id"] == inv_id

    # And the investigation status reflects completion.
    inv = client.get(f"/investigations/{inv_id}", headers=auth_headers).json()
    assert inv["status"] == "completed"


def test_decisions_locked_after_run(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    created = _create(client, auth_headers)
    inv_id = created["id"]
    decisions = {entry.id: "approved" for entry in CATALOG}
    client.post(
        f"/investigations/{inv_id}/approvals",
        headers=auth_headers,
        json={"decisions": decisions},
    )
    client.post(f"/investigations/{inv_id}/run", headers=auth_headers)

    # Now decisions are read-only (409).
    response = client.post(
        f"/investigations/{inv_id}/approvals",
        headers=auth_headers,
        json={"decisions": {CATALOG[0].id: "rejected"}},
    )
    assert response.status_code == 409


def test_delete_requires_auth(client: TestClient) -> None:
    assert client.delete("/investigations/1").status_code == 401


def test_delete_removes_the_investigation_and_its_report(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    created = _create(client, auth_headers)
    inv_id = created["id"]

    # Give it children to clean up: approvals, a job, and a report
    # (a full demo run).
    decisions = {entry.id: "approved" for entry in CATALOG}
    client.post(
        f"/investigations/{inv_id}/approvals",
        headers=auth_headers,
        json={"decisions": decisions},
    )
    run = client.post(f"/investigations/{inv_id}/run", headers=auth_headers)
    assert run.status_code == 202, run.text
    assert client.get(f"/reports/{inv_id}", headers=auth_headers).status_code == 200

    deleted = client.delete(f"/investigations/{inv_id}", headers=auth_headers)
    assert deleted.status_code == 204, deleted.text
    assert deleted.text == ""

    # Gone from every view: detail, approvals, and the queue listing.
    assert (
        client.get(f"/investigations/{inv_id}", headers=auth_headers).status_code
        == 404
    )
    assert (
        client.get(
            f"/investigations/{inv_id}/approvals", headers=auth_headers
        ).status_code
        == 404
    )
    rows = client.get("/investigations", headers=auth_headers).json()
    assert inv_id not in [row["id"] for row in rows]

    # The report row went with it — this endpoint reads only the reports
    # table, so a 404 here proves the child was cleaned up too.
    assert client.get(f"/reports/{inv_id}", headers=auth_headers).status_code == 404


def test_delete_unknown_investigation_is_404(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    assert client.delete("/investigations/9999", headers=auth_headers).status_code == 404


def test_delete_refuses_while_a_run_is_active(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _create(client, auth_headers)
    monkeypatch.setattr(
        DatabaseStore,
        "investigation_has_active_job",
        lambda self, inv_id: True,
    )
    response = client.delete(f"/investigations/{created['id']}", headers=auth_headers)
    assert response.status_code == 409

    # The investigation survived the refused delete.
    assert (
        client.get(f"/investigations/{created['id']}", headers=auth_headers).status_code
        == 200
    )
