"""Cancellation and timeout handling for diagnostic runs (#29).

The collection loop is cooperative: `run_collection` consults
`should_abort` between sources and enforces an overall deadline, and
`_execute_run` — the worker-thread body behind POST /run — wires those
to the job row, so `POST /jobs/{id}/cancel` actually stops a run while
keeping whatever evidence it gathered.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import live
from app.live import (
    ConnectionSettings,
    RunTimeout,
    run_collection,
)
from app.main import create_app
from app.routers.investigations import _execute_run
from app.store import CATALOG, DatabaseStore

# ── Local fakes (kept self-contained so this file owns its contract) ─


class FakeCursor:
    def __init__(self, rows=None, description=None, error=None):
        self.rows = rows or [(1,)]
        self.description = description or [("n",)]
        self.error = error
        self.executed: list[str] = []

    def execute(self, sql, *args):
        self.executed.append(sql)
        if self.error is not None:
            raise self.error

    def fetchmany(self, size):
        return self.rows[:size]


class FakeConnection:
    def __init__(self, cursors=None):
        self._cursors = cursors or [FakeCursor()]
        self.timeout: int | None = None
        self.closed = False

    def cursor(self):
        if len(self._cursors) > 1:
            return self._cursors.pop(0)
        return self._cursors[0]

    def close(self):
        self.closed = True


def entry(source_id: str) -> dict:
    return {
        "id": source_id,
        "title": f"Source {source_id}",
        "category": "tests",
        "issue_area": "blocking",
        "sql": "SELECT 1 AS ok",
    }


def sql_settings() -> ConnectionSettings:
    return ConnectionSettings(
        server_host="sql-prod-01",
        server_port=1433,
        auth_type="sql",
        username="svc_diag",
        password="not-a-real-password",
    )


# ── run_collection: cooperative abort and deadline ──────────────────


def test_run_collection_stops_between_sources_on_abort() -> None:
    conn = FakeConnection([FakeCursor(), FakeCursor(), FakeCursor()])
    seen = {"n": 0}

    def abort_after_one() -> bool:
        seen["n"] += 1
        return seen["n"] > 1

    outcomes = run_collection(
        [entry("a"), entry("b"), entry("c")],
        sql_settings(),
        lookback_hours=4,
        connect_fn=lambda settings: conn,
        should_abort=abort_after_one,
    )

    # The first source ran; the loop stopped at the next boundary.
    assert [o.source_id for o in outcomes] == ["a"]
    assert conn.closed


def test_run_deadline_raises_with_the_partial_outcomes() -> None:
    conn = FakeConnection()
    with pytest.raises(RunTimeout) as excinfo:
        run_collection(
            [entry("a"), entry("b")],
            sql_settings(),
            lookback_hours=4,
            connect_fn=lambda settings: conn,
            deadline_seconds=0,  # budget already spent before source one
        )
    assert excinfo.value.outcomes == []
    assert conn.closed


def test_mark_uncollected_pads_the_sources_the_run_never_reached() -> None:
    conn = FakeConnection()
    (outcome,) = run_collection(
        [entry("a")],
        sql_settings(),
        lookback_hours=1,
        connect_fn=lambda settings: conn,
    )

    padded = live.mark_uncollected(
        [entry("a"), entry("b")], [outcome], "run stopped early"
    )

    assert [o.source_id for o in padded] == ["a", "b"]
    assert padded[1].status == "unavailable"
    assert padded[1].note == "run stopped early"


def test_run_timeout_seconds_reads_the_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LIVE_RUN_TIMEOUT_SECONDS", "120")
    assert live.run_timeout_seconds() == 120
    monkeypatch.setenv("LIVE_RUN_TIMEOUT_SECONDS", "nonsense")
    assert live.run_timeout_seconds() == live.DEFAULT_RUN_TIMEOUT_SECONDS
    monkeypatch.setenv("LIVE_RUN_TIMEOUT_SECONDS", "-5")
    assert live.run_timeout_seconds() == live.DEFAULT_RUN_TIMEOUT_SECONDS


# ── _execute_run: the worker-thread job lifecycle ───────────────────


def _prepare_job(client: TestClient, headers: dict[str, str]) -> tuple[int, str]:
    """Create + fully approve an investigation, then open a job for it."""
    created = client.post(
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
    assert created.status_code == 201, created.text
    inv_id = created.json()["id"]
    client.post(
        f"/investigations/{inv_id}/approvals",
        headers=headers,
        json={"decisions": {e.id: "approved" for e in CATALOG}},
    )

    session = client.app.state.SessionLocal()
    try:
        job = DatabaseStore(session).create_job(inv_id)
        session.commit()
        return inv_id, job.job_id
    finally:
        session.close()


def _flip_job_to(client: TestClient, job_id: str, status: str) -> None:
    session = client.app.state.SessionLocal()
    try:
        DatabaseStore(session).update_job(job_id, status=status)
        session.commit()
    finally:
        session.close()


def _catalog_entries() -> list[dict]:
    return [
        {
            "id": e.id,
            "title": e.title,
            "category": e.category,
            "issue_area": e.issue_area,
            "sql": e.sql,
        }
        for e in CATALOG
    ]


def test_execute_run_keeps_a_preexisting_canceled_state(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    inv_id, job_id = _prepare_job(client, auth_headers)
    _flip_job_to(client, job_id, "canceled")

    _execute_run(
        session_factory=client.app.state.SessionLocal,
        job_id=job_id,
        inv_id=inv_id,
        organization="Acme Corp",
        entries=None,
        settings=None,
        lookback_hours=4,
        databases=None,
    )

    job = client.get(f"/jobs/{job_id}", headers=auth_headers).json()
    assert job["status"] == "canceled"  # the runner never overwrote it
    inv = client.get(f"/investigations/{inv_id}", headers=auth_headers).json()
    assert inv["status"] == "canceled"


def test_execute_run_stops_mid_collection_on_cancel_and_keeps_evidence(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    inv_id, job_id = _prepare_job(client, auth_headers)

    def fake_run_collection(
        entries,
        settings,
        lookback_hours,
        databases=None,
        *,
        deadline_seconds=None,
        should_abort=None,
    ):
        first = live.SourceOutcome(
            source_id=entries[0]["id"],
            title=entries[0]["title"],
            category=entries[0]["category"],
            issue_area=entries[0]["issue_area"],
            status="collected",
            evidence=[{"n": 1}],
            row_count=1,
        )
        # A cancel request lands while the first source was running.
        _flip_job_to(client, job_id, "canceled")
        assert should_abort is not None and should_abort()
        return [first]

    monkeypatch.setattr(live, "run_collection", fake_run_collection)

    _execute_run(
        session_factory=client.app.state.SessionLocal,
        job_id=job_id,
        inv_id=inv_id,
        organization="Acme Corp",
        entries=_catalog_entries(),
        settings=sql_settings(),
        lookback_hours=4,
        databases=None,
    )

    job = client.get(f"/jobs/{job_id}", headers=auth_headers).json()
    assert job["status"] == "canceled"
    inv = client.get(f"/investigations/{inv_id}", headers=auth_headers).json()
    assert inv["status"] == "canceled"

    # Partial evidence survived: one collected source, and every source
    # the run never reached is an explicit coverage gap (spec §3.8).
    report = client.get(f"/reports/{inv_id}", headers=auth_headers).json()
    collected = [s for s in report["data_sources"] if s["status"] == "collected"]
    assert len(collected) == 1
    assert len(report["coverage_gaps"]) == len(CATALOG) - 1
    assert all("stopped before" in gap["note"] for gap in report["coverage_gaps"])


def test_execute_run_timeout_fails_the_job_and_keeps_partial_report(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    inv_id, job_id = _prepare_job(client, auth_headers)

    def fake_run_collection(*args, **kwargs):
        raise live.RunTimeout([])

    monkeypatch.setattr(live, "run_collection", fake_run_collection)

    _execute_run(
        session_factory=client.app.state.SessionLocal,
        job_id=job_id,
        inv_id=inv_id,
        organization="Acme Corp",
        entries=_catalog_entries(),
        settings=sql_settings(),
        lookback_hours=4,
        databases=None,
    )

    job = client.get(f"/jobs/{job_id}", headers=auth_headers).json()
    assert job["status"] == "failed"
    assert "overall timeout" in job["error"]
    inv = client.get(f"/investigations/{inv_id}", headers=auth_headers).json()
    assert inv["status"] == "failed"

    # The partial report exists and is honest: nothing was collected, so
    # every source is a coverage gap.
    report = client.get(f"/reports/{inv_id}", headers=auth_headers).json()
    assert all(s["status"] == "unavailable" for s in report["data_sources"])
    assert len(report["coverage_gaps"]) == len(CATALOG)


def test_cancel_endpoint_still_reflects_the_run_state(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    _inv_id, job_id = _prepare_job(client, auth_headers)
    canceled = client.post(f"/jobs/{job_id}/cancel", headers=auth_headers)
    assert canceled.status_code == 200
    assert canceled.json()["status"] == "canceled"


def test_run_completes_against_a_file_database(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worker thread's own session must see the job it was given.

    The shared in-memory test database hides cross-connection visibility
    bugs: with a file-backed SQLite (as in compose and on Render) the
    worker opens a real second connection, and the run only starts if
    the job row was committed before the worker was scheduled (#29).
    """
    monkeypatch.delenv("LIVE_DATABASE_URL", raising=False)
    app = create_app(database_url=f"sqlite:///{tmp_path}/sdip.db")
    with TestClient(app) as client:
        headers = {
            "Authorization": "Bearer "
            + client.post(
                "/auth/login",
                json={"username": "dba", "password": "dba-password"},
            ).json()["access_token"]
        }
        created = client.post(
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
        assert created.status_code == 201, created.text
        inv_id = created.json()["id"]
        client.post(
            f"/investigations/{inv_id}/approvals",
            headers=headers,
            json={"decisions": {e.id: "approved" for e in CATALOG}},
        )

        started = client.post(f"/investigations/{inv_id}/run", headers=headers)
        assert started.status_code == 202, started.text
        job_id = started.json()["job_id"]

        job = client.get(f"/jobs/{job_id}", headers=headers).json()
        assert job["status"] == "succeeded"
        assert (
            client.get(f"/reports/{inv_id}", headers=headers).status_code == 200
        )
