"""HTTP route that downloads the incident report as a PDF (#20).

``GET /reports/{investigation_id}.pdf`` reads the #19 payload from the report
repository, renders it through :func:`app.report.pdf.render`, and returns it as
an attachment. A missing report is a ``404``; a failed render is a ``500`` with a
generic message. Neither path ever returns a raw exception or traceback to the
client, and the payload is credential-free by construction.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response

from app.report.dependencies import get_report_repository
from app.report.pdf import PdfRenderError, render, report_filename
from app.report.repository import ReportRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["reports"])

#: Generic client-facing message; the real reason is never echoed.
RENDER_FAILED_DETAIL = "the report could not be rendered"


@router.get("/{investigation_id}.pdf")
def download_report_pdf(
    investigation_id: int,
    repository: Annotated[ReportRepository, Depends(get_report_repository)],
) -> Response:
    """Return the investigation's report as a downloadable PDF."""
    payload = repository.get_payload(investigation_id)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no report for investigation {investigation_id}",
        )

    try:
        pdf = render(payload)
    except PdfRenderError:
        logger.warning(
            "report.pdf render failed for investigation %s", investigation_id
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=RENDER_FAILED_DETAIL,
        ) from None

    response = Response(content=pdf, media_type="application/pdf")
    response.headers["Content-Disposition"] = (
        f"attachment; filename={report_filename(payload, investigation_id)}"
    )
    return response
