"""HTTP routes for background diagnostic runs and job status (#11).

``POST /investigations/{id}/run`` is the only way a run starts. It enqueues one
``diagnostic_run`` job behind the #10 approval gate and returns ``202`` with the
arq job id without waiting on the run. ``GET /jobs/{job_id}`` reports the job's
status from its Redis-backed arq state.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.diagnostics.catalog import Catalog
from app.investigations.dependencies import (
    get_approval_repository,
    get_catalog,
    get_investigation_repository,
)
from app.investigations.repository import (
    ApprovalRepository,
    InvestigationRepository,
)
from app.investigations.service import UnknownInvestigation
from app.jobs.dependencies import get_job_queue, get_job_status_reader
from app.jobs.queue import JobQueue
from app.jobs.service import (
    RunAlreadyStarted,
    RunGateClosed,
    get_job_status,
    start_run,
)
from app.jobs.status import JobNotFound, JobStatusReader, JobStatusResponse

router = APIRouter(tags=["jobs"])


@router.post(
    "/investigations/{investigation_id}/run",
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_run_endpoint(
    investigation_id: int,
    investigation_repository: Annotated[
        InvestigationRepository, Depends(get_investigation_repository)
    ],
    approval_repository: Annotated[
        ApprovalRepository, Depends(get_approval_repository)
    ],
    catalog: Annotated[Catalog, Depends(get_catalog)],
    job_queue: Annotated[JobQueue, Depends(get_job_queue)],
) -> dict[str, str]:
    """Enqueue a diagnostic run for an approved investigation (202 + job id)."""
    try:
        queued = await start_run(
            investigation_id,
            investigation_repository=investigation_repository,
            approval_repository=approval_repository,
            catalog=catalog,
            job_queue=job_queue,
        )
    except UnknownInvestigation as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except (RunGateClosed, RunAlreadyStarted) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return {"job_id": queued.job_id}


@router.get("/jobs/{job_id}", status_code=status.HTTP_200_OK)
async def get_job_endpoint(
    job_id: str,
    reader: Annotated[JobStatusReader, Depends(get_job_status_reader)],
) -> JobStatusResponse:
    """Return a job's current status (result on success, error on failure)."""
    try:
        return await get_job_status(job_id, reader=reader)
    except JobNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc