"""FastAPI dependencies for the report PDF endpoint (#20)."""

from __future__ import annotations

from fastapi import Request

from app.report.repository import (
    ReportRepository,
    SqlAlchemyReportRepository,
)


def get_report_repository(request: Request) -> ReportRepository:
    """Return the payload store, created once per app and cached on app state.

    Defaults to :class:`~app.report.repository.InMemoryReportRepository` until
    #21 provides the persistent store; the endpoint only needs ``get_payload``.
    """
    repository = getattr(request.app.state, "report_repository", None)
    if repository is None:
        repository = SqlAlchemyReportRepository()
        request.app.state.report_repository = repository
    return repository
