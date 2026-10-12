"""Starting and observing background diagnostic runs (#11).

The service is transport-agnostic: the router maps :class:`RunGateClosed` and
:class:`RunAlreadyStarted` to HTTP 409 and :class:`UnknownInvestigation` to 404.
Starting a run is the only way diagnostic work is scheduled: it checks the #10
approval gate (:func:`can_start`), enqueues exactly one ``diagnostic_run`` job
carrying only the investigation id, and marks the investigation running.
"""

from __future__ import annotations

from app.diagnostics.catalog import Catalog
from app.investigations.repository import (
    ApprovalRepository,
    AuditEntry,
    AuditWriter,
    InvestigationRecord,
    InvestigationRepository,
    NullAuditWriter,
)
from app.investigations.service import UnknownInvestigation, can_start
from app.jobs.queue import JobQueue, QueuedJob
from app.jobs.status import JobStatusReader, JobStatusResponse
from app.worker import diagnostic_run

#: Investigation status once a run is enqueued.
RUNNING_STATUS = "running"


class RunGateClosed(Exception):
    """The investigation is not approved to run (mapped to 409)."""


class RunAlreadyStarted(Exception):
    """The investigation already has a run underway (mapped to 409)."""


def require_startable(
    investigation_id: int,
    *,
    investigation_repository: InvestigationRepository,
    approval_repository: ApprovalRepository,
    catalog: Catalog,
) -> InvestigationRecord:
    """Return the investigation when a run may start, else raise.

    Order matters: an unknown id is a 404, a pending/rejected approval set or an
    already-running investigation is a 409. The approval rule itself is the #10
    gate (:func:`can_start`), never reimplemented here.
    """
    investigation = investigation_repository.get_investigation(investigation_id)
    if investigation is None:
        raise UnknownInvestigation(f"no investigation with id {investigation_id}")
    if not can_start(
        investigation_id,
        investigation_repository=investigation_repository,
        approval_repository=approval_repository,
        catalog=catalog,
    ):
        raise RunGateClosed(
            f"investigation {investigation_id} is not approved: every required "
            "catalog query must be approved before a run can start"
        )
    if investigation.status != "draft":
        raise RunAlreadyStarted(
            f"investigation {investigation_id} already has a run "
            f"(status={investigation.status!r})"
        )
    return investigation


async def start_run(
    investigation_id: int,
    *,
    caller_actor: str = "system",
    investigation_repository: InvestigationRepository,
    approval_repository: ApprovalRepository,
    catalog: Catalog,
    job_queue: JobQueue,
    audit_writer: AuditWriter | None = None,
) -> QueuedJob:
    """Enqueue a diagnostic run for an approved investigation.

    The job payload is only ``investigation_id`` — no credentials. The
    investigation moves to ``running`` only after the enqueue succeeds, so a
    failed enqueue leaves it startable.
    """
    investigation = require_startable(
        investigation_id,
        investigation_repository=investigation_repository,
        approval_repository=approval_repository,
        catalog=catalog,
    )
    queued = await job_queue.enqueue_job(diagnostic_run, investigation_id)
    investigation_repository.update_status(investigation_id, RUNNING_STATUS)
    writer = audit_writer or NullAuditWriter()
    writer.write(
        AuditEntry(
            organization_id=investigation.organization_id,
            actor=caller_actor,
            action="run.start",
            target=f"investigation:{investigation_id}",
        )
    )
    return queued


async def get_job_status(job_id: str, *, reader: JobStatusReader) -> JobStatusResponse:
    """Return the job's current status from the (Redis-backed) reader."""
    return await reader.get_status(job_id)