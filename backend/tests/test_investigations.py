"""Tests for investigations: create/list/get, approvals, and run gating."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.store import CATALOG


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
    client: TestClient, auth_headers: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    """The backend's own log names the investigated server on create and
    on run start, so an observer can watch without the frontend (#34)."""
    with caplog.at_level(logging.INFO, logger="sqldiag.investigations"):
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

    messages = [record.getMessage() for record in caplog.records]
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
