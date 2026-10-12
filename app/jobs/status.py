"""Query a job's status from its arq (Redis-backed) state (#11).

Status is derived from the arq job itself — its queue entry, in-progress key,
and stored result — not from application memory, so ``GET /jobs/{id}`` works
after a worker (or API) restart as long as the job survives in Redis.

:class:`ArqJobStatusReader` is the real path. Unit tests inject their own
:class:`JobStatusReader` so the suite runs with no Redis server.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Protocol, runtime_checkable

from arq.connections import ArqRedis, RedisSettings, create_pool
from arq.jobs import Job
from arq.jobs import JobStatus as ArqJobStatus
from pydantic import BaseModel


class JobState(str, Enum):
    """The job statuses the API exposes."""

    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class JobStatusResponse(BaseModel):
    """Current status of one job, with its result on success or error on failure."""

    job_id: str
    status: JobState
    investigation_id: int | None = None
    result: Any | None = None
    error: str | None = None


class JobNotFound(RuntimeError):
    """No job with the requested id exists in Redis (mapped to 404)."""


def _investigation_id_from_args(args: tuple[Any, ...]) -> int | None:
    if not args:
        return None
    first = args[0]
    if isinstance(first, int) and not isinstance(first, bool):
        return first
    return None


def _error_text(result: Any) -> str:
    """A readable, secret-free error string for a failed job."""
    if isinstance(result, BaseException):
        message = str(result)
        return message or type(result).__name__
    return repr(result)


@runtime_checkable
class JobStatusReader(Protocol):
    """Reads the current status of a job (Redis-backed in production)."""

    async def get_status(self, job_id: str) -> JobStatusResponse:
        """Return the job's status, or raise :class:`JobNotFound`."""


class ArqJobStatusReader:
    """Real status reader backed by the arq Redis client over ``REDIS_URL``."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._redis: ArqRedis | None = None

    async def _pool(self) -> ArqRedis:
        if self._redis is None:
            self._redis = await create_pool(RedisSettings.from_dsn(self._redis_url))
        return self._redis

    async def get_status(self, job_id: str) -> JobStatusResponse:
        redis = await self._pool()
        job = Job(job_id, redis)

        result_info = await job.result_info()
        if result_info is not None:
            investigation_id = _investigation_id_from_args(result_info.args)
            if result_info.success:
                return JobStatusResponse(
                    job_id=job_id,
                    status=JobState.succeeded,
                    investigation_id=investigation_id,
                    result=result_info.result,
                )
            return JobStatusResponse(
                job_id=job_id,
                status=JobState.failed,
                investigation_id=investigation_id,
                error=_error_text(result_info.result),
            )

        arq_state = await job.status()
        if arq_state is ArqJobStatus.not_found:
            raise JobNotFound(f"no job with id {job_id!r}")

        info = await job.info()
        investigation_id = (
            _investigation_id_from_args(info.args) if info is not None else None
        )
        if arq_state is ArqJobStatus.in_progress:
            return JobStatusResponse(
                job_id=job_id,
                status=JobState.running,
                investigation_id=investigation_id,
            )
        return JobStatusResponse(
            job_id=job_id,
            status=JobState.queued,
            investigation_id=investigation_id,
        )