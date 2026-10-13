"""Reports router: JSON payload and PDF download.

The ``.pdf`` route is registered before the ``{inv_id}`` route so the more
specific ``/reports/{inv_id}.pdf`` path wins over the bare integer route.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response

from app.auth import CurrentUser, get_current_user
from app.pdf import render_pdf
from app.store import InMemoryStore, get_store

router = APIRouter(tags=["reports"])


def _report_lines(report: dict) -> list[str]:
    lines = [
        f"Organization: {report['organization']}",
        f"Server: {report['server_host']}:{report['server_port']}",
        f"Scope: {report['database_scope']}",
        f"Lookback (hours): {report['lookback_hours']}",
        f"Severity: {report['severity']}",
        f"Confidence: {report['confidence_percent']}% ({report['confidence_label']})",
        "",
        "Primary cause:",
        f"  {report['primary_cause']['title']}",
        "",
        "Rationale:",
        f"  {report['rationale']}",
    ]
    return lines


@router.get("/reports/{inv_id}.pdf")
def download_report_pdf(
    inv_id: int,
    store: InMemoryStore = Depends(get_store),
    _: CurrentUser = Depends(get_current_user),
) -> Response:
    report = store.get_report(inv_id)
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    pdf = render_pdf(f"Investigation {inv_id} Report", _report_lines(report))
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="investigation-{inv_id}.pdf"'
        },
    )


@router.get("/reports/{inv_id}")
def get_report(
    inv_id: int,
    store: InMemoryStore = Depends(get_store),
    _: CurrentUser = Depends(get_current_user),
) -> dict:
    report = store.get_report(inv_id)
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return report
