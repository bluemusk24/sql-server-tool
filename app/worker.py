"""arq background worker: executes queued diagnostics off the HTTP path (#11).

Contract pinned here (groomed spec for #11):

* arq version: ``arq>=0.28.0`` in ``pyproject.toml``.
* Task-name contract: arq resolves queued jobs by dotted module path, so every
  enqueuable task keeps the exact name ``app.worker.<function>``. The enqueue
  path in :mod:`app.jobs.queue` asserts this rather than trusting a string.
* Redis: the worker connects using ``REDIS_URL`` from #2 settings via
  :func:`make_worker_settings`. No credential ever appears in a job payload:
  jobs carry only the investigation id and the worker resolves credentials
  out-of-band from the session-only store (#5).
* Run it with the arq CLI: ``arq app.worker.WorkerSettings`` (README.md).
"""

from __future__ import annotations

import asyncio
from typing import Any

from arq.connections import RedisSettings

from app.config import Settings, load_settings
from app.credentials import CredentialStore, SqlCredentials
from app.diagnostics.catalog import Catalog, load_catalog
from app.execution import Connector, run_diagnostic
from app.investigations.repository import (
    ApprovalRepository,
    AuditEntry,
    AuditWriter,
    InvestigationRepository,
    NullAuditWriter,
    SqlAlchemyApprovalRepository,
    SqlAlchemyInvestigationRepository,
)
from app.persistence import (
    PersistenceRepository,
    SqlAlchemyAuditWriter,
    SqlAlchemyPersistenceRepository,
)

#: Result returned by :func:`example_task`; the Redis-backed test asserts it.
EXAMPLE_TASK_MARKER = "example-task-done"

__all__ = [
    "EXAMPLE_TASK_MARKER",
    "TaskCredentialsMissing",
    "WorkerSettings",
    "diagnostic_run",
    "example_task",
    "make_worker_settings",
    "retention_cleanup",
]


async def example_task(ctx: dict[str, Any]) -> str:
    """Trivial task that proves the queue round-trip end to end (#11)."""
    del ctx
    await asyncio.sleep(0.1)
    return EXAMPLE_TASK_MARKER


class TaskCredentialsMissing(RuntimeError):
    """Raised when no session credentials exist for an investigation."""


async def diagnostic_run(ctx: dict[str, Any], investigation_id: int) -> dict[str, Any]:
    """Execute the approved diagnostic batch for ``investigation_id``.

    The job payload carries only the investigation id; credentials stay
    session-only and are never queued. The worker fetches them from the
    in-memory credential store (#5), fails fast when they are missing, and
    delegates the run to the #12 engine (:func:`app.execution.run_diagnostic`),
    which enforces the #10 approval gate itself. The investigation row is moved
    to its terminal status and the engine's evidence + source inventory are
    returned for the #21 persistence layer.
    """
    repository: InvestigationRepository = ctx["investigation_repository"]
    credential_store: CredentialStore = ctx["credential_store"]
    approval_repository: ApprovalRepository = ctx["approval_repository"]
    catalog: Catalog = ctx["catalog"]
    connector: Connector | None = ctx.get("connector")
    audit_writer: AuditWriter = ctx.get("audit_writer") or NullAuditWriter()
    persistence: PersistenceRepository | None = ctx.get("persistence_repository")

    try:
        _require_credentials(credential_store, investigation_id)
        result = run_diagnostic(
            investigation_id,
            investigation_repository=repository,
            approval_repository=approval_repository,
            catalog=catalog,
            credential_store=credential_store,
            connector=connector,
        )
        repository.update_status(investigation_id, "succeeded")
        try:
            audit_writer.write(
                AuditEntry(
                    organization_id=0,
                    actor="system",
                    action="run.complete",
                    target=f"investigation:{investigation_id}",
                )
            )
        except Exception:  # pragma: no cover - best effort  # noqa: BLE001, S110
            pass
        # Persist evidence if repository is available
        if persistence is not None:
            try:
                for ev in getattr(result, "evidence", []) or []:
                    persistence.record_evidence_run(investigation_id, ev)
                for sig in getattr(result, "signals", []) or []:
                    persistence.record_finding(investigation_id, sig)
                if getattr(result, "verdict", None) is not None:
                    persistence.record_verdict(
                        investigation_id, result.verdict
                    )
            except Exception:  # pragma: no cover - persistence failures shouldn't break run  # noqa: BLE001, S110
                pass
        return result.model_dump()
    except Exception:
        repository.update_status(investigation_id, "failed")
        try:
            audit_writer.write(
                AuditEntry(
                    organization_id=0,
                    actor="system",
                    action="run.fail",
                    target=f"investigation:{investigation_id}",
                )
            )
        except Exception:  # pragma: no cover  # noqa: BLE001, S110
            pass
        raise


async def retention_cleanup(ctx: dict[str, Any]) -> dict[str, Any]:
    """Remove rows older than the retention window from all five tables (#21)."""
    persistence: PersistenceRepository = ctx.get("persistence_repository") or SqlAlchemyPersistenceRepository()
    report = persistence.cleanup()
    return {"deleted": report.deleted, "total": report.total}


def _require_credentials(credential_store: CredentialStore, investigation_id: int) -> SqlCredentials:
    credentials = credential_store.get(str(investigation_id))
    if credentials is None:
        raise TaskCredentialsMissing(
            f"cannot run investigation {investigation_id}: no session "
            "credentials are stored for it (credentials are never queued)"
        )
    if not isinstance(credentials, SqlCredentials):
        raise TaskCredentialsMissing(
            f"cannot run investigation {investigation_id}: AD/Kerberos "
            "authentication needs #28 and is not supported yet"
        )
    return credentials


def make_worker_settings(
    settings: Settings,
    *,
    credential_store: CredentialStore | None = None,
    investigation_repository: InvestigationRepository | None = None,
    approval_repository: ApprovalRepository | None = None,
    catalog: Catalog | None = None,
    connector: Connector | None = None,
    audit_writer: AuditWriter | None = None,
    persistence_repository: PersistenceRepository | None = None,
) -> dict[str, Any]:
    """Build the arq worker settings for ``settings`` (config-driven).

    The repository, catalog, credential store, and connector are injectable so
    unit tests can run the worker against fakes; the CLI path uses the real
    implementations (the connector defaults to the #6 module inside the engine).
    """
    return {
        "functions": [example_task, diagnostic_run, retention_cleanup],
        "redis_settings": RedisSettings.from_dsn(settings.redis_url),
        "queue_name": settings.worker_queue_name,
        "max_jobs": settings.worker_max_jobs,
        "job_timeout": settings.job_timeout_seconds,
        "max_tries": settings.job_max_tries,
        "retry_jobs": settings.job_retry_jobs,
        "ctx": {
            "credential_store": (
                CredentialStore() if credential_store is None else credential_store
            ),
            "investigation_repository": (
                SqlAlchemyInvestigationRepository()
                if investigation_repository is None
                else investigation_repository
            ),
            "approval_repository": (
                SqlAlchemyApprovalRepository()
                if approval_repository is None
                else approval_repository
            ),
            "catalog": load_catalog() if catalog is None else catalog,
            "connector": connector,
            "audit_writer": audit_writer
            or SqlAlchemyAuditWriter(),
            "persistence_repository": persistence_repository
            or SqlAlchemyPersistenceRepository(),
        },
    }


#: Importable worker settings for the arq CLI: ``arq app.worker.WorkerSettings``.
#: Computed from the environment (same fail-fast pattern as ``app.main``).
WorkerSettings: dict[str, Any] = make_worker_settings(load_settings())