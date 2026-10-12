"""FastAPI dependencies for the background-job endpoints (#11).

The queue and status reader are created once per app and cached on app state.
Both default to Redis-backed implementations built from ``REDIS_URL``; tests
override them with in-memory fakes so no Redis is needed.
"""

from __future__ import annotations

from fastapi import Request

from app.jobs.queue import ArqJobQueue, JobQueue
from app.jobs.status import ArqJobStatusReader, JobStatusReader


def get_job_queue(request: Request) -> JobQueue:
    """Return the run-enqueue queue, created once per app."""
    queue = getattr(request.app.state, "job_queue", None)
    if queue is None:
        queue = ArqJobQueue(request.app.state.settings.redis_url)
        request.app.state.job_queue = queue
    return queue


def get_job_status_reader(request: Request) -> JobStatusReader:
    """Return the Redis-backed job status reader, created once per app."""
    reader = getattr(request.app.state, "job_status_reader", None)
    if reader is None:
        reader = ArqJobStatusReader(request.app.state.settings.redis_url)
        request.app.state.job_status_reader = reader
    return reader