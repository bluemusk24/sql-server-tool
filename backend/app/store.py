"""In-memory store seeded with demo data.

This is the persistence layer for the MVP. It holds organizations, users,
investigations (with their catalog), approval decisions, jobs, and report
payloads entirely in process memory. Replacing this module with a real
database-backed implementation is the intended evolution and should not
require touching the routers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import Request

from app.auth import hash_password
from app.models import CatalogEntry

# ── Seed catalog (mirrors frontend/src/services/mockData.ts) ────────
_SEED_CATALOG: list[dict[str, Any]] = [
    {
        "id": "blocking_now",
        "key": "blocking_now",
        "title": "Current blocking chains",
        "description": "Identify head blockers and their victims right now.",
        "purpose": "Detect active blocking that is preventing queries from completing.",
        "category": "blocking",
        "issue_area": "blocking",
        "sql": "SELECT blocking_session_id, wait_type, wait_time_ms\nFROM sys.dm_exec_requests\nWHERE blocking_session_id <> 0;",
        "sql_text": "SELECT blocking_session_id, wait_type, wait_time_ms\nFROM sys.dm_exec_requests\nWHERE blocking_session_id <> 0;",
        "params": [],
        "requires_approval": True,
        "expected_impact": "Read-only DMV query; negligible load.",
        "versions": ["2016", "2017", "2019", "2022"],
        "min_permission": "VIEW SERVER STATE",
        "read_only": True,
    },
    {
        "id": "deadlock_history",
        "key": "deadlock_history",
        "title": "Deadlock graph history",
        "description": "Pull recent deadlock graphs from the system health session.",
        "purpose": "Find deadlocks that occurred within the lookback window.",
        "category": "blocking",
        "issue_area": "deadlocks",
        "sql": "SELECT xed.xml_data.value('(//deadlock)[1]', 'XML') AS deadlock_graph\nFROM sys.dm_xe_session_targets xet\nJOIN sys.dm_xe_sessions xe ON xe.address = xet.event_session_address\nWHERE xe.name = 'system_health';",
        "sql_text": "SELECT xed.xml_data.value('(//deadlock)[1]', 'XML') AS deadlock_graph\nFROM sys.dm_xe_session_targets xet\nJOIN sys.dm_xe_sessions xe ON xe.address = xet.event_session_address\nWHERE xe.name = 'system_health';",
        "params": [],
        "requires_approval": True,
        "expected_impact": "Read-only; parses ring buffer.",
        "versions": ["2016", "2017", "2019", "2022"],
        "min_permission": "VIEW SERVER STATE",
        "read_only": True,
    },
    {
        "id": "top_queries",
        "key": "top_queries",
        "title": "Top resource-consuming queries",
        "description": "Rank queries by CPU, reads, writes, and duration.",
        "purpose": "Surface the queries consuming the most resources.",
        "category": "queries",
        "issue_area": "queries",
        "sql": "SELECT TOP 5 qs.execution_count,\n       qs.total_worker_time / 1000.0 AS total_cpu_ms,\n       qs.total_logical_reads,\n       st.text\nFROM sys.dm_exec_query_stats qs\nCROSS APPLY sys.dm_exec_sql_text(qs.sql_handle) st\nORDER BY qs.total_worker_time DESC;",
        "sql_text": "SELECT TOP 5 qs.execution_count,\n       qs.total_worker_time / 1000.0 AS total_cpu_ms,\n       qs.total_logical_reads,\n       st.text\nFROM sys.dm_exec_query_stats qs\nCROSS APPLY sys.dm_exec_sql_text(qs.sql_handle) st\nORDER BY qs.total_worker_time DESC;",
        "params": [],
        "requires_approval": True,
        "expected_impact": "Read-only; scans query plan cache.",
        "versions": ["2016", "2017", "2019", "2022"],
        "min_permission": "VIEW SERVER STATE",
        "read_only": True,
    },
    {
        "id": "cpu_schedulers",
        "key": "cpu_schedulers",
        "title": "CPU and scheduler pressure",
        "description": "Check runnable tasks and scheduler waits per CPU.",
        "purpose": "Detect CPU saturation at the SQL Server scheduler level.",
        "category": "queries",
        "issue_area": "cpu",
        "sql": "SELECT scheduler_id,\n       current_tasks_count,\n       runnable_tasks_count,\n       current_workers_count\nFROM sys.dm_os_schedulers\nWHERE scheduler_id < 255;",
        "sql_text": "SELECT scheduler_id,\n       current_tasks_count,\n       runnable_tasks_count,\n       current_workers_count\nFROM sys.dm_os_schedulers\nWHERE scheduler_id < 255;",
        "params": [],
        "requires_approval": True,
        "expected_impact": "Read-only DMV query; negligible load.",
        "versions": ["2016", "2017", "2019", "2022"],
        "min_permission": "VIEW SERVER STATE",
        "read_only": True,
    },
    {
        "id": "failed_agent_jobs",
        "key": "failed_agent_jobs",
        "title": "Failed SQL Agent jobs",
        "description": "List failed job runs from the lookback window.",
        "purpose": "Flag every failed SQL Agent job and highlight repeats.",
        "category": "agent_jobs",
        "issue_area": "agent_jobs",
        "sql": "SELECT j.name AS job_name,\n       h.run_status,\n       h.message\nFROM msdb.dbo.sysjobhistory h\nJOIN msdb.dbo.sysjobs j ON j.job_id = h.job_id\nWHERE h.run_status = 0;",
        "sql_text": "SELECT j.name AS job_name,\n       h.run_status,\n       h.message\nFROM msdb.dbo.sysjobhistory h\nJOIN msdb.dbo.sysjobs j ON j.job_id = h.job_id\nWHERE h.run_status = 0;",
        "params": [],
        "requires_approval": True,
        "expected_impact": "Read-only; scans msdb job history.",
        "versions": ["2016", "2017", "2019", "2022"],
        "min_permission": "SELECT on msdb",
        "read_only": True,
    },
    {
        "id": "sql_server_errors",
        "key": "sql_server_errors",
        "title": "SQL Server error log",
        "description": "Scan the current error log for error-level entries.",
        "purpose": "Flag SQL Server errors within the lookback window.",
        "category": "agent_jobs",
        "issue_area": "errors",
        "sql": "EXEC sp_readerrorlog 0, 1, 'error';",
        "sql_text": "EXEC sp_readerrorlog 0, 1, 'error';",
        "params": [],
        "requires_approval": True,
        "expected_impact": "Read-only; reads the SQL Server error log.",
        "versions": ["2016", "2017", "2019", "2022"],
        "min_permission": "VIEW SERVER STATE",
        "read_only": True,
    },
    {
        "id": "database_status",
        "key": "database_status",
        "title": "Database availability status",
        "description": "Check each database's state (online, offline, suspect).",
        "purpose": "Detect databases that are unavailable or in a suspect state.",
        "category": "availability",
        "issue_area": "availability",
        "sql": "SELECT name, state_desc\nFROM sys.databases;",
        "sql_text": "SELECT name, state_desc\nFROM sys.databases;",
        "params": [],
        "requires_approval": True,
        "expected_impact": "Read-only DMV query; negligible load.",
        "versions": ["2016", "2017", "2019", "2022"],
        "min_permission": "VIEW ANY DATABASE",
        "read_only": True,
    },
    {
        "id": "storage_pressure",
        "key": "storage_pressure",
        "title": "Storage free-space percentage",
        "description": "Report free-space percentage for each database file.",
        "purpose": "Flag databases with less than 10% free space.",
        "category": "availability",
        "issue_area": "storage",
        "sql": "SELECT DB_NAME(database_id) AS db_name,\n       name AS file_name,\n       size * 8 / 1024 AS size_mb,\n       FILEPROPERTY(name, 'SpaceUsed') * 8 / 1024 AS used_mb\nFROM sys.master_files;",
        "sql_text": "SELECT DB_NAME(database_id) AS db_name,\n       name AS file_name,\n       size * 8 / 1024 AS size_mb,\n       FILEPROPERTY(name, 'SpaceUsed') * 8 / 1024 AS used_mb\nFROM sys.master_files;",
        "params": [],
        "requires_approval": True,
        "expected_impact": "Read-only DMV query; negligible load.",
        "versions": ["2016", "2017", "2019", "2022"],
        "min_permission": "VIEW SERVER STATE",
        "read_only": True,
    },
]

CATALOG: list[CatalogEntry] = [CatalogEntry(**entry) for entry in _SEED_CATALOG]


# ── Dataclasses held in memory ───────────────────────────────────────
@dataclass
class User:
    username: str
    organization: str
    actor: str
    password_salt: str
    password_hash: str


@dataclass
class Investigation:
    id: int
    status: str
    server_host: str
    server_port: int
    auth_type: str
    databases: list[str] | None
    lookback_hours: int
    catalog: list[CatalogEntry] = field(default_factory=lambda: list(CATALOG))


@dataclass
class Job:
    job_id: str
    status: str
    investigation_id: int | None
    result: Any | None = None
    error: str | None = None


@dataclass
class ApprovalDecision:
    catalog_id: str
    decision: str
    actor: str
    note: str | None
    created_at: str


class InMemoryStore:
    """A single process-local store instance, seeded on construction."""

    def __init__(self) -> None:
        self.users: dict[str, User] = {}
        self.tokens: dict[str, str] = {}  # token -> username
        self.organizations: dict[int, dict[str, Any]] = {}
        self.investigations: dict[int, Investigation] = {}
        self.approvals: dict[int, dict[str, ApprovalDecision]] = {}
        self.jobs: dict[str, Job] = {}
        self.reports: dict[int, dict[str, Any]] = {}
        self._next_org_id = 1
        self._next_investigation_id = 1
        self._seed()

    # ── Seeding ─────────────────────────────────────────────────────
    def _seed(self) -> None:
        self._create_org("Acme Corp", "https://idp.acme.example")

        # Two demo users with hashed passwords.
        salt, digest = hash_password("dba-password")
        self.users["dba"] = User(
            username="dba",
            organization="Acme Corp",
            actor="dba@acme.example",
            password_salt=salt,
            password_hash=digest,
        )
        salt2, digest2 = hash_password("admin-password")
        self.users["admin"] = User(
            username="admin",
            organization="Acme Corp",
            actor="admin@acme.example",
            password_salt=salt2,
            password_hash=digest2,
        )

        # A completed investigation (id 1) with an approval set and a report,
        # so the dashboard and report view have data to show immediately.
        inv = Investigation(
            id=self._next_investigation_id,
            status="completed",
            server_host="sql01.acme.example",
            server_port=1433,
            auth_type="ad",
            databases=None,
            lookback_hours=4,
        )
        self._next_investigation_id += 1
        self.investigations[inv.id] = inv

        # Seed approvals: every catalog entry approved by the dba.
        now = datetime.now(UTC).isoformat()
        self.approvals[inv.id] = {
            entry.id: ApprovalDecision(
                catalog_id=entry.id,
                decision="approved",
                actor="dba@acme.example",
                note=None,
                created_at=now,
            )
            for entry in CATALOG
        }

        # Seed a report for the completed investigation (mirrors mockData.ts).
        self.reports[inv.id] = self._build_demo_report(inv, "Acme Corp")

    def _create_org(self, name: str, issuer: str) -> int:
        org_id = self._next_org_id
        self._next_org_id += 1
        self.organizations[org_id] = {"id": org_id, "name": name, "issuer": issuer}
        return org_id

    # ── Organizations ───────────────────────────────────────────────
    def list_organizations(self) -> list[dict[str, Any]]:
        return sorted(self.organizations.values(), key=lambda o: o["id"])

    def create_organization(self, name: str, issuer: str) -> dict[str, Any]:
        org_id = self._create_org(name, issuer)
        return self.organizations[org_id]

    def delete_organization(self, org_id: int) -> bool:
        return self.organizations.pop(org_id, None) is not None

    # ── Investigations ──────────────────────────────────────────────
    def list_investigations(self) -> list[dict[str, Any]]:
        return [
            {"id": inv.id, "status": inv.status}
            for inv in sorted(self.investigations.values(), key=lambda i: i.id)
        ]

    def create_investigation(
        self,
        server_host: str,
        server_port: int,
        auth_type: str,
        databases: list[str] | None,
        lookback_hours: int,
    ) -> Investigation:
        inv = Investigation(
            id=self._next_investigation_id,
            status="draft",
            server_host=server_host,
            server_port=server_port,
            auth_type=auth_type,
            databases=databases,
            lookback_hours=lookback_hours,
        )
        self._next_investigation_id += 1
        self.investigations[inv.id] = inv
        self.approvals.setdefault(inv.id, {})
        return inv

    def get_investigation(self, inv_id: int) -> Investigation | None:
        return self.investigations.get(inv_id)

    # ── Approvals ───────────────────────────────────────────────────
    def get_approvals(self, inv_id: int) -> dict[str, ApprovalDecision] | None:
        if inv_id not in self.investigations:
            return None
        return self.approvals.setdefault(inv_id, {})

    # ── Jobs ────────────────────────────────────────────────────────
    def create_job(self, investigation_id: int) -> Job:
        job_id = f"job-{investigation_id}-{len(self.jobs) + 1}"
        job = Job(job_id=job_id, status="queued", investigation_id=investigation_id)
        self.jobs[job_id] = job
        return job

    def get_job(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    # ── Reports ─────────────────────────────────────────────────────
    def get_report(self, inv_id: int) -> dict[str, Any] | None:
        return self.reports.get(inv_id)

    def generate_report(self, inv: Investigation, organization: str) -> dict[str, Any]:
        """Build a demo report for an investigation and store it."""
        report = self._build_demo_report(inv, organization)
        self.reports[inv.id] = report
        return report

    # ── Report builder (demo content) ───────────────────────────────
    def _build_demo_report(self, inv: Investigation, organization: str) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        return {
            "investigation_id": inv.id,
            "organization": organization,
            "server_host": inv.server_host,
            "server_port": inv.server_port,
            "database_scope": "instance" if not inv.databases else "databases",
            "databases": inv.databases or [],
            "lookback_hours": inv.lookback_hours,
            "investigation_time": now,
            "data_sources": [
                {
                    "source_id": e.id,
                    "title": e.title,
                    "category": e.category,
                    "issue_area": e.issue_area,
                    "status": "collected",
                    "checked": True,
                    "note": "",
                }
                for e in CATALOG
            ],
            "findings_by_area": [],
            "primary_cause": {
                "title": "No primary cause determined",
                "description": "Demo report generated by the in-memory store.",
                "confidence_percent": 0,
                "confidence_label": "low",
            },
            "alternatives": [],
            "confidence_percent": 0,
            "confidence_label": "low",
            "severity": "low",
            "rationale": "This is a seeded demo report from the in-memory backend.",
            "inconclusive": True,
            "coverage_gaps": [],
            "recommendations": [],
            "validation_checks": [],
            "remediation": [],
            "read_only_disclaimer": "recommendation only, execute at the DBA's discretion",
        }


def get_store(request: Request) -> InMemoryStore:
    """FastAPI dependency returning the app's store singleton."""
    return request.app.state.store
