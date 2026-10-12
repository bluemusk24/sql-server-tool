"""Report assembly and payload schema (#19).

Assembles the read-only incident report payload from a completed #12 run and the
#18 verdict, so #20 (PDF), #25 (dashboard), and #26 (synthetic evaluation) share
one shape. :func:`assemble` is pure data-in -> data-out; :func:`build_report`
adds the built-in, no-network correlation path.
"""

from app.report.assembly import (
    assemble,
    build_report,
    collect_evidence,
    collect_signals,
)
from app.report.pdf import render, render_html, report_filename
from app.report.repository import InMemoryReportRepository, ReportRepository
from app.report.schemas import (
    READ_ONLY_DISCLAIMER,
    AreaFindings,
    AreaSeverity,
    CoverageGap,
    DataSourceReport,
    EvidenceItem,
    InvestigationContext,
    Recommendation,
    RemediationStep,
    ReportPayload,
)

__all__ = [
    "READ_ONLY_DISCLAIMER",
    "AreaFindings",
    "AreaSeverity",
    "CoverageGap",
    "DataSourceReport",
    "EvidenceItem",
    "InMemoryReportRepository",
    "InvestigationContext",
    "Recommendation",
    "RemediationStep",
    "ReportPayload",
    "ReportRepository",
    "assemble",
    "build_report",
    "collect_evidence",
    "collect_signals",
    "render",
    "render_html",
    "report_filename",
]
