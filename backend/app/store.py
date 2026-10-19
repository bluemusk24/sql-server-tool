"""Database-backed store (SQLAlchemy), seeded with demo data.

This is the persistence layer. It speaks SQLAlchemy against whatever
``DATABASE_URL`` points at (SQLite by default, Postgres later). Routers talk
only to :class:`DatabaseStore`, so the HTTP contract is unchanged from the
previous in-memory implementation.
"""

from __future__ import annotations

import os
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import db_models as orm
from app.db import get_db
from app.models import CatalogEntry
from app.security import hash_password, verify_password

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


def _now() -> str:
    return datetime.now(UTC).isoformat()


# ── Dataclasses returned to routers (detached from the session) ─────
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
    created_at: str = ""
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


# ── Row → dataclass conversions ─────────────────────────────────────
def _user_from(row: orm.UserRow) -> User:
    return User(
        username=row.username,
        organization=row.organization,
        actor=row.actor,
        password_salt=row.password_salt,
        password_hash=row.password_hash,
    )


def _inv_from(row: orm.InvestigationRow) -> Investigation:
    return Investigation(
        id=row.id,
        status=row.status,
        server_host=row.server_host,
        server_port=row.server_port,
        auth_type=row.auth_type,
        databases=row.databases,
        lookback_hours=row.lookback_hours,
        created_at=row.created_at,
    )


def _job_from(row: orm.JobRow) -> Job:
    return Job(
        job_id=row.job_id,
        status=row.status,
        investigation_id=row.investigation_id,
        result=row.result,
        error=row.error,
    )


def _decision_from(row: orm.ApprovalDecisionRow) -> ApprovalDecision:
    return ApprovalDecision(
        catalog_id=row.catalog_id,
        decision=row.decision,
        actor=row.actor,
        note=row.note,
        created_at=row.created_at,
    )


def _normalize_database_scope(databases: list[str] | str | None) -> list[str] | None:
    """Normalize an investigation's database scope to a clean list of names.

    ``None`` means "the whole instance" and is passed through unchanged. The
    API sends a list of names; some older clients sent the same scope as a
    comma-joined string, so both shapes are accepted here. Blank entries are
    dropped and surrounding whitespace is trimmed.
    """
    if databases is None:
        return None
    names = databases.split(",") if isinstance(databases, str) else databases
    return [name.strip() for name in names if name.strip()]


class DatabaseStore:
    """Session-backed store. One instance per request, bound to that request's
    session (see :func:`get_store`). Reads return detached dataclasses; writes
    are explicit methods so persistence is unambiguous."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # ── Auth ────────────────────────────────────────────────────────
    def get_user(self, username: str) -> User | None:
        row = self.session.get(orm.UserRow, username)
        return _user_from(row) if row is not None else None

    def resolve_token(self, token: str) -> User | None:
        row = self.session.execute(
            select(orm.UserRow)
            .join(orm.AuthTokenRow, orm.AuthTokenRow.username == orm.UserRow.username)
            .where(orm.AuthTokenRow.token == token)
        ).scalar_one_or_none()
        return _user_from(row) if row is not None else None

    def issue_token(self, username: str) -> str:
        token = secrets.token_urlsafe(32)
        self.session.add(
            orm.AuthTokenRow(token=token, username=username, created_at=_now())
        )
        self.session.flush()
        return token

    def upsert_sso_user(self, username: str, organization: str, actor: str) -> None:
        """Create or refresh the local user behind an OIDC login (#30).

        SSO users get an unusable random password: the schema's columns
        are NOT NULL, and no human ever knows the value, so such a user
        can only ever authenticate through the issuer. Existing rows keep
        their credentials; the organization and actor follow the latest
        token.
        """
        row = self.session.get(orm.UserRow, username)
        if row is None:
            self.session.add(
                orm.UserRow(
                    username=username,
                    organization=organization,
                    actor=actor,
                    password_salt=secrets.token_hex(32),
                    password_hash=secrets.token_hex(32),
                )
            )
        else:
            row.organization = organization
            row.actor = actor
        self.session.flush()

    # ── Organizations ───────────────────────────────────────────────
    def list_organizations(self) -> list[dict[str, Any]]:
        rows = self.session.execute(
            select(orm.OrganizationRow).order_by(orm.OrganizationRow.id)
        ).scalars()
        return [{"id": r.id, "name": r.name, "issuer": r.issuer} for r in rows]

    def create_organization(self, name: str, issuer: str) -> dict[str, Any]:
        row = orm.OrganizationRow(name=name, issuer=issuer)
        self.session.add(row)
        self.session.flush()
        return {"id": row.id, "name": row.name, "issuer": row.issuer}

    def delete_organization(self, org_id: int) -> bool:
        row = self.session.get(orm.OrganizationRow, org_id)
        if row is None:
            return False
        self.session.delete(row)
        return True

    def get_organization_by_issuer(self, issuer: str) -> dict[str, Any] | None:
        """Resolve an OIDC caller's organization by its issuer (#30).

        Trailing slashes are insignificant in issuer URLs, so both sides
        are normalized before comparison.
        """
        wanted = issuer.rstrip("/")
        for row in self.session.execute(
            select(orm.OrganizationRow).order_by(orm.OrganizationRow.id)
        ).scalars():
            if row.issuer.rstrip("/") == wanted:
                return {"id": row.id, "name": row.name, "issuer": row.issuer}
        return None

    # ── Investigations ──────────────────────────────────────────────
    def list_investigations(self) -> list[dict[str, Any]]:
        """Every investigation's target + state, so callers can show which
        SQL Server a job is for without a second lookup (#34)."""
        rows = self.session.execute(
            select(orm.InvestigationRow).order_by(orm.InvestigationRow.id)
        ).scalars()
        return [
            {
                "id": r.id,
                "status": r.status,
                "server_host": r.server_host,
                "server_port": r.server_port,
                "auth_type": r.auth_type,
                "databases": r.databases,
                "lookback_hours": r.lookback_hours,
                "created_at": r.created_at,
            }
            for r in rows
        ]

    def create_investigation(
        self,
        server_host: str,
        server_port: int,
        auth_type: str,
        databases: list[str] | str | None,
        lookback_hours: int,
    ) -> Investigation:
        # Older clients (and the SSMS export) sent the scope as a
        # comma-joined string; accept both shapes and store a clean
        # list of names.
        databases = _normalize_database_scope(databases)
        row = orm.InvestigationRow(
            status="draft",
            server_host=server_host,
            server_port=server_port,
            auth_type=auth_type,
            databases=databases,
            lookback_hours=lookback_hours,
            created_at=_now(),
        )
        self.session.add(row)
        self.session.flush()
        return _inv_from(row)

    def get_investigation(self, inv_id: int) -> Investigation | None:
        row = self.session.get(orm.InvestigationRow, inv_id)
        return _inv_from(row) if row is not None else None

    def set_investigation_status(self, inv_id: int, status: str) -> None:
        row = self.session.get(orm.InvestigationRow, inv_id)
        if row is not None:
            row.status = status
            self.session.flush()

    def delete_investigation(self, inv_id: int) -> bool:
        """Remove an investigation and everything stored under it (#39).

        The children reference the investigation by foreign key without a
        cascade, so they go first — approvals, jobs, then the report — and
        only then the investigation row itself, all in the session's
        transaction. Returns False when there is nothing to delete.
        """
        row = self.session.get(orm.InvestigationRow, inv_id)
        if row is None:
            return False
        for model in (orm.ApprovalDecisionRow, orm.JobRow, orm.ReportRow):
            self.session.execute(
                delete(model).where(model.investigation_id == inv_id)
            )
        self.session.delete(row)
        self.session.flush()
        return True

    # ── Approvals ───────────────────────────────────────────────────
    def get_approvals(self, inv_id: int) -> dict[str, ApprovalDecision] | None:
        if self.session.get(orm.InvestigationRow, inv_id) is None:
            return None
        return self._query_approvals(inv_id)

    def _query_approvals(self, inv_id: int) -> dict[str, ApprovalDecision]:
        rows = self.session.execute(
            select(orm.ApprovalDecisionRow).where(
                orm.ApprovalDecisionRow.investigation_id == inv_id
            )
        ).scalars()
        return {r.catalog_id: _decision_from(r) for r in rows}

    def record_approvals(
        self,
        inv_id: int,
        decisions: dict[str, str],
        actor: str,
        note: str | None,
    ) -> dict[str, ApprovalDecision]:
        now = _now()
        for catalog_id, decision in decisions.items():
            row = self.session.execute(
                select(orm.ApprovalDecisionRow).where(
                    orm.ApprovalDecisionRow.investigation_id == inv_id,
                    orm.ApprovalDecisionRow.catalog_id == catalog_id,
                )
            ).scalar_one_or_none()
            if row is None:
                self.session.add(
                    orm.ApprovalDecisionRow(
                        investigation_id=inv_id,
                        catalog_id=catalog_id,
                        decision=decision,
                        actor=actor,
                        note=note,
                        created_at=now,
                    )
                )
            else:
                row.decision = decision
                row.actor = actor
                row.note = note
                row.created_at = now
        self.session.flush()
        return self._query_approvals(inv_id)

    # ── Jobs ────────────────────────────────────────────────────────
    def create_job(self, investigation_id: int) -> Job:
        job_id = f"job-{investigation_id}-{uuid.uuid4().hex[:8]}"
        row = orm.JobRow(
            job_id=job_id, status="queued", investigation_id=investigation_id
        )
        self.session.add(row)
        self.session.flush()
        return _job_from(row)

    def get_job(self, job_id: str) -> Job | None:
        row = self.session.get(orm.JobRow, job_id)
        return _job_from(row) if row is not None else None

    def update_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        result: Any | None = None,
        error: str | None = None,
    ) -> Job | None:
        row = self.session.get(orm.JobRow, job_id)
        if row is None:
            return None
        if status is not None:
            row.status = status
        if result is not None:
            row.result = result
        if error is not None:
            row.error = error
        self.session.flush()
        return _job_from(row)

    def investigation_has_active_job(self, inv_id: int) -> bool:
        """True when a run for this investigation is still queued or live."""
        rows = self.session.execute(
            select(orm.JobRow).where(
                orm.JobRow.investigation_id == inv_id,
                orm.JobRow.status.in_(("queued", "running")),
            )
        ).scalars()
        return rows.first() is not None

    # ── Reports ─────────────────────────────────────────────────────
    def get_report(self, inv_id: int) -> dict[str, Any] | None:
        row = self.session.get(orm.ReportRow, inv_id)
        return dict(row.payload) if row is not None else None

    def generate_report(
        self,
        inv: Investigation,
        organization: str,
        collection: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Persist the report for an investigation.

        With `collection` (live run outcomes, #31) the report carries the
        real per-source statuses, coverage gaps, and evidence; without it
        a deterministic, clearly-labeled synthetic report (#26) keeps the
        pipeline, dashboard, and PDF exercisable end to end.
        """
        report = build_demo_report(inv, organization)
        if collection is not None:
            report = apply_collection(report, collection)
        else:
            from app.synthetic import generate_synthetic_report

            report = generate_synthetic_report(inv, organization)
        row = self.session.get(orm.ReportRow, inv.id)
        if row is None:
            self.session.add(
                orm.ReportRow(investigation_id=inv.id, payload=report)
            )
        else:
            row.payload = report
        self.session.flush()
        return report


# ── Report builder (demo content) ───────────────────────────────────
def build_demo_report(inv: Investigation, organization: str) -> dict[str, Any]:
    now = _now()
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
            "description": "Demo report generated by the backend.",
            "confidence_percent": 0,
            "confidence_label": "low",
        },
        "alternatives": [],
        "confidence_percent": 0,
        "confidence_label": "low",
        "severity": "low",
        "rationale": "This is a seeded demo report.",
        "inconclusive": True,
        "coverage_gaps": [],
        "recommendations": [],
        "validation_checks": [],
        "remediation": [],
        "read_only_disclaimer": "recommendation only, execute at the DBA's discretion",
    }


def apply_collection(
    report: dict[str, Any], collection: list[dict[str, Any]]
) -> dict[str, Any]:
    """Blend real live-collection outcomes into the report (#31).

    Sources carry their true status; anything not collected becomes an
    explicit coverage gap (spec §3.8 — never claim unavailable evidence
    was checked). Collected rows land grouped by issue area as raw
    evidence. No signals or causes are invented: deriving findings needs
    validated thresholds first (spec §10).
    """
    report = dict(report)
    report["data_sources"] = [
        {
            "source_id": outcome["source_id"],
            "title": outcome["title"],
            "category": outcome["category"],
            "issue_area": outcome["issue_area"],
            "status": outcome["status"],
            "checked": outcome["status"] == "collected",
            "note": outcome["note"],
        }
        for outcome in collection
    ]
    report["coverage_gaps"] = [
        {
            "source_id": outcome["source_id"],
            "issue_area": outcome["issue_area"],
            "status": outcome["status"],
            "note": outcome["note"] or "no evidence collected",
        }
        for outcome in collection
        if outcome["status"] != "collected"
    ]

    areas: dict[str, dict[str, Any]] = {}
    for outcome in collection:
        if outcome["status"] != "collected" or not outcome["evidence"]:
            continue
        area = areas.setdefault(
            outcome["issue_area"],
            {
                "area": outcome["issue_area"],
                "severity": "none",
                "confidence_percent": 0,
                "confidence_label": "low",
                "signal_count": 0,
                "findings": [],
                "evidence": [],
            },
        )
        area["evidence"].extend(
            {
                "evidence_id": f"{outcome['source_id']}-{index}",
                "area": outcome["issue_area"],
                "timestamp": report["investigation_time"],
                "description": outcome["title"],
                "details": row,
            }
            for index, row in enumerate(outcome["evidence"])
        )
    report["findings_by_area"] = list(areas.values())

    collected = sum(1 for outcome in collection if outcome["status"] == "collected")
    gaps = len(collection) - collected
    report["rationale"] = (
        f"Live collection: {collected} of {len(collection)} sources collected"
        + (f"; {gaps} unavailable (see coverage gaps)." if gaps else ".")
    )
    report["inconclusive"] = gaps > 0 or collected == 0
    report["primary_cause"] = {
        "title": "No primary cause determined",
        "description": (
            "Evidence was collected, but no decision rules have been "
            "validated for these signals yet (docs/spec.md §10)."
        ),
        "confidence_percent": 0,
        "confidence_label": "low",
    }
    return report


# ── Seeding (runs once, on an empty database) ───────────────────────
def seed_database(session: Session) -> None:
    """Insert demo data into an empty database; no-op if already seeded."""
    if (
        session.execute(select(orm.UserRow).limit(1)).scalar_one_or_none()
        is not None
    ):
        return

    session.add(
        orm.OrganizationRow(name="Acme Corp", issuer="https://idp.acme.example")
    )

    # Demo credentials by default; a production copy overrides them via the
    # environment (#41) so a shared URL never ships with published passwords.
    dba_password = os.getenv("SEED_DBA_PASSWORD") or "dba-password"
    admin_password = os.getenv("SEED_ADMIN_PASSWORD") or "admin-password"

    salt, digest = hash_password(dba_password)
    session.add(
        orm.UserRow(
            username="dba",
            organization="Acme Corp",
            actor="dba@acme.example",
            password_salt=salt,
            password_hash=digest,
        )
    )
    salt2, digest2 = hash_password(admin_password)
    session.add(
        orm.UserRow(
            username="admin",
            organization="Acme Corp",
            actor="admin@acme.example",
            password_salt=salt2,
            password_hash=digest2,
        )
    )

    # A completed investigation (id 1) with approvals and a report, so the
    # dashboard and report view have data to show immediately.
    inv_row = orm.InvestigationRow(
        status="completed",
        server_host="sql01.acme.example",
        server_port=1433,
        auth_type="ad",
        databases=None,
        lookback_hours=4,
        created_at=_now(),
    )
    session.add(inv_row)
    session.flush()  # assign inv_row.id

    now = _now()
    for entry in CATALOG:
        session.add(
            orm.ApprovalDecisionRow(
                investigation_id=inv_row.id,
                catalog_id=entry.id,
                decision="approved",
                actor="dba@acme.example",
                note=None,
                created_at=now,
            )
        )

    inv = _inv_from(inv_row)
    session.add(
        orm.ReportRow(investigation_id=inv.id, payload=build_demo_report(inv, "Acme Corp"))
    )


def apply_seed_overrides(session: Session) -> None:
    """Enforce the ``SEED_*_PASSWORD`` overrides on the seeded logins.

    Runs on every boot and is idempotent. Whenever an override variable
    is set, the matching seeded login is made to carry that value: the
    first-boot race of #41 (database seeded before the variables
    existed) resolves on the next restart, and an operator rotates a
    password later by simply changing the variable and redeploying
    (#43) — the environment is the source of truth, so a login cannot
    keep a password the operator has moved past. Environments without
    the variables (dev, local compose) are left exactly as seeded.
    """
    overrides = [
        ("dba", os.getenv("SEED_DBA_PASSWORD")),
        ("admin", os.getenv("SEED_ADMIN_PASSWORD")),
    ]
    for username, override in overrides:
        if not override:
            continue
        row = session.execute(
            select(orm.UserRow).where(orm.UserRow.username == username)
        ).scalar_one_or_none()
        if row is None:
            continue
        if verify_password(override, row.password_salt, row.password_hash):
            continue
        salt, digest = hash_password(override)
        row.password_salt = salt
        row.password_hash = digest


def get_store(db: Session = Depends(get_db)) -> DatabaseStore:
    """FastAPI dependency returning a store bound to the request's session."""
    return DatabaseStore(db)
