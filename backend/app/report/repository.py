"""Payload store behind the PDF download endpoint (#20).

#19 assembles a :class:`~app.report.schemas.ReportPayload` but does not persist
it; #21 adds the real, database-backed store. The endpoint depends on the small
:class:`ReportRepository` interface rather than on the #19 assembly inputs, so it
is unit-testable with :class:`InMemoryReportRepository` and needs no SQL Server.
When #21 lands it supplies a repository with the same ``get_payload`` shape and
the endpoint is unchanged.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import select

from app.db.models import ReportPayloadRecord
from app.db.session import session_scope
from app.report.schemas import ReportPayload


@runtime_checkable
class ReportRepository(Protocol):
    """Read access the PDF endpoint needs: payload by investigation id."""

    def get_payload(self, investigation_id: int) -> ReportPayload | None:
        """Return the assembled payload for ``investigation_id``, or ``None``."""


class SqlAlchemyReportRepository:
    """Persistent report payload repository backed by the DB (#21)."""

    def __init__(
        self,
        session_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._session_factory = session_factory or session_scope

    def get_payload(self, investigation_id: int) -> ReportPayload | None:
        with self._session_factory() as session:
            statement = select(ReportPayloadRecord).where(
                ReportPayloadRecord.investigation_id == investigation_id
            )
            model = session.execute(statement).scalars().first()
            if model is None or not model.payload:
                return None
            return ReportPayload.model_validate(model.payload)


class InMemoryReportRepository:
    """In-memory payload store for unit tests and the pre-#21 runtime."""

    def __init__(self) -> None:
        self._payloads: dict[int, ReportPayload] = {}

    def put(self, investigation_id: int, payload: ReportPayload) -> None:
        """Store ``payload`` for ``investigation_id`` (test/worker helper)."""
        self._payloads[investigation_id] = payload

    def store_report_payload(
        self, investigation_id: int, payload: ReportPayload | dict[str, Any]
    ) -> None:
        """Persist a payload (mirrors SqlAlchemyPersistenceRepository API)."""
        from app.persistence import sanitize_json

        if isinstance(payload, ReportPayload):
            data = sanitize_json(payload.model_dump(mode="json"))
            self._payloads[investigation_id] = ReportPayload.model_validate(data)
            return
        data = sanitize_json(dict(payload))
        self._payloads[investigation_id] = ReportPayload.model_validate(data)

    def get_payload(self, investigation_id: int) -> ReportPayload | None:
        return self._payloads.get(investigation_id)


__all__ = [
    "InMemoryReportRepository",
    "ReportRepository",
    "SqlAlchemyReportRepository",
]

