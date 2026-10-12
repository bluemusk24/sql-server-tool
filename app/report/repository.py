"""Payload store behind the PDF download endpoint (#20).

#19 assembles a :class:`~app.report.schemas.ReportPayload` but does not persist
it; #21 adds the real, database-backed store. The endpoint depends on the small
:class:`ReportRepository` interface rather than on the #19 assembly inputs, so it
is unit-testable with :class:`InMemoryReportRepository` and needs no SQL Server.
When #21 lands it supplies a repository with the same ``get_payload`` shape and
the endpoint is unchanged.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.report.schemas import ReportPayload


@runtime_checkable
class ReportRepository(Protocol):
    """Read access the PDF endpoint needs: payload by investigation id."""

    def get_payload(self, investigation_id: int) -> ReportPayload | None:
        """Return the assembled payload for ``investigation_id``, or ``None``."""


class InMemoryReportRepository:
    """In-memory payload store for unit tests and the pre-#21 runtime."""

    def __init__(self) -> None:
        self._payloads: dict[int, ReportPayload] = {}

    def put(self, investigation_id: int, payload: ReportPayload) -> None:
        """Store ``payload`` for ``investigation_id`` (test/worker helper)."""
        self._payloads[investigation_id] = payload

    def get_payload(self, investigation_id: int) -> ReportPayload | None:
        return self._payloads.get(investigation_id)
