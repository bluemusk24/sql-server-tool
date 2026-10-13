"""Jobs router: status and cancellation for background runs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import CurrentUser, get_current_user
from app.models import JobStatusResponse
from app.store import InMemoryStore, get_store

router = APIRouter(tags=["jobs"])


def _to_response(job) -> JobStatusResponse:
    return JobStatusResponse(
        job_id=job.job_id,
        status=job.status,  # type: ignore[arg-type]
        investigation_id=job.investigation_id,
        result=job.result,
        error=job.error,
    )


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
def get_job(
    job_id: str,
    store: InMemoryStore = Depends(get_store),
    _: CurrentUser = Depends(get_current_user),
) -> JobStatusResponse:
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return _to_response(job)


@router.post("/jobs/{job_id}/cancel", response_model=JobStatusResponse)
def cancel_job(
    job_id: str,
    store: InMemoryStore = Depends(get_store),
    _: CurrentUser = Depends(get_current_user),
) -> JobStatusResponse:
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if job.status in ("queued", "running"):
        job.status = "canceled"
    return _to_response(job)
