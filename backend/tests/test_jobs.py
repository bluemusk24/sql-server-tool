"""Tests for the jobs router (status + cancel)."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_job_requires_auth(client: TestClient) -> None:
    assert client.get("/jobs/job-1").status_code == 401


def test_unknown_job_404(client: TestClient, auth_headers: dict[str, str]) -> None:
    assert client.get("/jobs/nope", headers=auth_headers).status_code == 404


def test_cancel_unknown_job_404(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    assert (
        client.post("/jobs/nope/cancel", headers=auth_headers).status_code == 404
    )


def _run_investigation(client: TestClient, headers: dict[str, str]) -> str:
    """Create + approve + run a seeded investigation, returning the job id."""
    from app.store import CATALOG

    created = client.post(
        "/investigations",
        headers=headers,
        json={
            "server_host": "sql",
            "server_port": 1433,
            "auth_type": "sql",
            "lookback_hours": 1,
        },
    ).json()
    inv_id = created["id"]
    decisions = {entry.id: "approved" for entry in CATALOG}
    client.post(
        f"/investigations/{inv_id}/approvals",
        headers=headers,
        json={"decisions": decisions},
    )
    run = client.post(f"/investigations/{inv_id}/run", headers=headers)
    return run.json()["job_id"]


def test_cancel_succeeded_job_is_noop(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    job_id = _run_investigation(client, auth_headers)
    # Job already succeeded; cancelling leaves it succeeded.
    response = client.post(f"/jobs/{job_id}/cancel", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
