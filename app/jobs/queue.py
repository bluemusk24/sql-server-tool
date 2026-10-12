"""The arq job queue used by the run-start path (#11).

Enqueueing is the only way a run starts: the HTTP handler never executes an
investigation inline. :class:`ArqJobQueue` talks to Redis via the arq client;
:class:`RecordingJobQueue` is the in-memory fake the unit suite uses so tests
run with no Redis server and can assert the exact job payloads.

Credentials never appear in a job payload: a diagnostic run job carries only
the investigation id, and the worker resolves credentials out-of-band from the
session-only store (#5).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from arq.connections import ArqRedis, RedisSettings, create_pool

#: Coroutine-function tasks that may be enqueued (``app.worker`` contract).
JobTask = Callable[..., Awaitable[Any]]

#: Module every enqueuable task must live in; arq resolves jobs by dotted path.
TASK_MODULE = "app.worker"


@dataclass(frozen=True)
class QueuedJob:
    """The id arq assigned to an enqueued job."""

    job_id: str


class JobEnqueueError(RuntimeError):
    """Raised when a job could not be enqueued (duplicate id, Redis down)."""


def task_path(task: JobTask) -> str:
    """The dotted name arq resolves at run time, asserted to live in ``app.worker``.

    Renaming or moving a task breaks this assertion on purpose: the worker
    contract (pinned in ``app/worker.py``) requires the path arq runs to match
    the path the API enqueues.
    """
    path = f"{task.__module__}.{task.__qualname__}"
    assert path.startswith(TASK_MODULE + "."), (
        f"task {path!r} is not under {TASK_MODULE!r}; arq resolves tasks by "
        "dotted module path and the worker contract pins them there"
    )
    return path


@runtime_checkable
class JobQueue(Protocol):
    """The enqueue surface the run-start path depends on."""

    async def enqueue_job(self, task: JobTask, *args: Any, **kwargs: Any) -> QueuedJob:
        """Enqueue ``task`` with ``args``/``kwargs`` and return its job id."""


class ArqJobQueue:
    """Real queue backed by the arq Redis client over ``REDIS_URL``."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._redis: ArqRedis | None = None

    async def _pool(self) -> ArqRedis:
        if self._redis is None:
            self._redis = await create_pool(RedisSettings.from_dsn(self._redis_url))
        return self._redis

    async def enqueue_job(self, task: JobTask, *args: Any, **kwargs: Any) -> QueuedJob:
        path = task_path(task)
        redis = await self._pool()
        job = await redis.enqueue_job(path, *args, **kwargs)
        if job is None:
            raise JobEnqueueError(f"job for {path} was not enqueued")
        return QueuedJob(job_id=job.job_id)


class RecordingJobQueue:
    """In-memory queue that records jobs instead of touching Redis.

    Keeps every enqueued ``(task, args, kwargs)`` so tests can assert the
    payload carries only the investigation id (no credentials).
    """

    def __init__(self) -> None:
        self.enqueued: list[tuple[JobTask, tuple[Any, ...], dict[str, Any]]] = []
        self._next_id = 1

    async def enqueue_job(self, task: JobTask, *args: Any, **kwargs: Any) -> QueuedJob:
        self.enqueued.append((task, args, kwargs))
        job = QueuedJob(job_id=f"fake-job-{self._next_id}")
        self._next_id += 1
        return job