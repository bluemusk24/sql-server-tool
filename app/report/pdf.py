"""Render the assembled report payload to PDF (#20).

:func:`render` turns a #19 :class:`~app.report.schemas.ReportPayload` into PDF
bytes with WeasyPrint, driven by the version-controlled template
``app/report/templates/report.html``. The template is the committed source of
the document structure and its print-friendly CSS; Python only fills its
``{{placeholder}}`` slots, so every plan.md §8 section (identity, scope, sources,
findings, causes, gaps and recommendations) is defined in the template.

Two properties the caller relies on:

* **No credential leakage.** Every value is passed through
  :func:`app.logredact.redact_text` *and* HTML-escaped before it reaches the
  document, on top of the redaction #19 already applied. A secret-shaped value
  therefore cannot reach the PDF even if a caller builds a payload directly.
* **Deterministic content.** The same payload produces the same HTML. The only
  timestamp in the document comes from the payload (``investigation_time``), not
  from the wall clock, so re-rendering does not change the report body.

WeasyPrint can fail to import when its system libraries (Pango/HarfBuzz/
fontconfig) are absent. That is detected at import time
(:data:`WEASYPRINT_AVAILABLE`) and surfaced as :class:`PdfRenderError`, so the
endpoint still returns a clean ``500`` and the test suite can skip visibly.
"""

from __future__ import annotations

import re
from datetime import datetime
from html import escape
from pathlib import Path

from app.logredact import redact_text
from app.report.schemas import (
    AreaFindings,
    ReportPayload,
)

__all__ = [
    "TEMPLATE_PATH",
    "WEASYPRINT_AVAILABLE",
    "WEASYPRINT_ERROR",
    "PdfRenderError",
    "render",
    "render_html",
    "report_filename",
]

#: The committed HTML template the PDF is rendered from.
TEMPLATE_PATH = Path(__file__).parent / "templates" / "report.html"

#: Evidence descriptions at least this long are marked so the stylesheet wraps
#: them instead of letting them overflow the page.
LONG_EVIDENCE_CHARS = 500

try:  # pragma: no cover - exercised by whichever environment is running
    from weasyprint import HTML

    WEASYPRINT_AVAILABLE = True
    WEASYPRINT_ERROR: str | None = None
except Exception as _exc:  # noqa: BLE001 - any import failure means no renderer
    HTML = None
    WEASYPRINT_AVAILABLE = False
    WEASYPRINT_ERROR = f"{type(_exc).__name__}: {_exc}"


class PdfRenderError(RuntimeError):
    """Rendering failed (missing system library or WeasyPrint error)."""


def _text(value: object) -> str:
    """Redact then HTML-escape a value bound for the document."""
    return escape(redact_text(str(value)), quote=True)


def _timestamp(value: datetime) -> str:
    """Format the payload's own timestamp; no wall-clock time is ever used."""
    return value.strftime("%Y-%m-%d %H:%M:%S %Z").strip()


def _database_scope(payload: ReportPayload) -> str:
    if payload.database_scope == "databases" and payload.databases:
        return ", ".join(_text(name) for name in payload.databases)
    return "Whole instance"


def _sources_table(payload: ReportPayload) -> str:
    if not payload.data_sources:
        return '<p class="empty">No data sources were recorded.</p>'
    rows = "".join(
        "<tr><td>{title}</td><td>{area}</td><td>{status}</td><td>{checked}</td></tr>".format(
            title=_text(source.title or source.source_id),
            area=_text(source.issue_area or source.category),
            status=_text(source.status.value),
            checked="yes" if source.checked else "no",
        )
        for source in payload.data_sources
    )
    return (
        "<table><thead><tr>"
        "<th>Source</th><th>Area</th><th>Status</th><th>Checked</th>"
        "</tr></thead><tbody>" + rows + "</tbody></table>"
    )


def _findings_table(payload: ReportPayload) -> str:
    if not payload.findings_by_area:
        return '<p class="empty">No issue areas were recorded.</p>'
    rows = "".join(
        "<tr><td>{area}</td><td class=\"severity-{severity}\">{severity}</td>"
        "<td>{percent}% ({label})</td><td>{count}</td></tr>".format(
            area=_text(area.area),
            severity=_text(area.severity.value),
            percent=area.confidence_percent,
            label=_text(area.confidence_label.value),
            count=area.signal_count,
        )
        for area in payload.findings_by_area
    )
    return (
        "<table><thead><tr>"
        "<th>Area</th><th>Severity</th><th>Confidence</th><th>Signals</th>"
        "</tr></thead><tbody>" + rows + "</tbody></table>"
    )


def _evidence_html(area: AreaFindings) -> str:
    if not area.evidence:
        return ""
    blocks: list[str] = []
    for item in area.evidence:
        description = redact_text(item.description)
        css_class = "evidence long" if len(description) >= LONG_EVIDENCE_CHARS else "evidence"
        when = f' <span class="when">({_timestamp(item.timestamp)})</span>' if item.timestamp else ""
        blocks.append(
            f'<p class="{css_class}">{_text(description)}{when}</p>'
        )
    return "".join(blocks)


def _findings_detail(payload: ReportPayload) -> str:
    blocks: list[str] = []
    for area in payload.findings_by_area:
        if not area.findings and not area.evidence:
            continue
        blocks.append(f"<h3>{_text(area.area)}</h3>")
        if area.findings:
            items = "".join(
                f'<li><span class="severity-{_text(signal.severity.value)}">'
                f"{_text(signal.severity.value)}</span>: {_text(signal.description)}</li>"
                for signal in area.findings
            )
            blocks.append(f"<ul>{items}</ul>")
        blocks.append(_evidence_html(area))
    if not blocks:
        return '<p class="empty">No findings were raised in this investigation.</p>'
    return "".join(blocks)


def _causes(payload: ReportPayload) -> str:
    blocks: list[str] = []
    if payload.inconclusive:
        blocks.append(
            '<p class="disclaimer">Result is inconclusive: evidence is '
            "insufficient or confidence is below 50%. Causes below are "
            "low-confidence alternatives.</p>"
        )
    primary = payload.primary_cause
    blocks.append(
        f"<h3>Primary: {_text(primary.area)} "
        f"({primary.confidence_percent}% {_text(primary.confidence_label.value)})</h3>"
    )
    blocks.append(f"<p>{_text(primary.description)}</p>")
    if payload.alternatives:
        items = "".join(
            f"<li>{_text(cause.area)} ({cause.confidence_percent}% "
            f"{_text(cause.confidence_label.value)}): {_text(cause.description)}</li>"
            for cause in payload.alternatives
        )
        blocks.append(f"<h3>Ranked alternatives</h3><ol>{items}</ol>")
    if payload.rationale:
        blocks.append(f'<p class="meta">Rationale: {_text(payload.rationale)}</p>')
    return "".join(blocks)


def _gaps(payload: ReportPayload) -> str:
    if not payload.coverage_gaps:
        return '<p class="empty">No coverage gaps were recorded.</p>'
    items = "".join(
        f"<li>{_text(gap.source_id)}"
        + (f" ({_text(gap.issue_area)})" if gap.issue_area else "")
        + f" &mdash; {_text(gap.status.value)}"
        + (f": {_text(gap.note)}" if gap.note else "")
        + "</li>"
        for gap in payload.coverage_gaps
    )
    return f"<ul>{items}</ul>"


def _remediation_html(steps: list, checks: list[str]) -> str:
    blocks: list[str] = []
    if checks:
        items = "".join(f"<li>{_text(check)}</li>" for check in checks)
        blocks.append(f"<h4>Validation checks</h4><ul>{items}</ul>")
    if steps:
        items: list[str] = []
        for step in steps:
            body = f"<li>{_text(step.step)}"
            if step.draft_sql:
                body += f'<pre class="sql">{_text(step.draft_sql)}</pre>'
            body += f'<p class="meta">{_text(step.disclaimer)}</p></li>'
            items.append(body)
        blocks.append("<h4>Remediation steps</h4><ol>" + "".join(items) + "</ol>")
    return "".join(blocks)


def _recommendations(payload: ReportPayload) -> str:
    blocks: list[str] = []
    if payload.recommendations:
        for index, recommendation in enumerate(payload.recommendations, start=1):
            area = recommendation.area or f"General {index}"
            blocks.append(f"<h3>{_text(area)}</h3>")
            blocks.append(
                _remediation_html(
                    recommendation.remediation, recommendation.validation_checks
                )
            )
    else:
        blocks.append(
            _remediation_html(payload.remediation, payload.validation_checks)
        )
    if not any(block for block in blocks):
        return '<p class="empty">No validation checks or remediation were recorded.</p>'
    return "".join(blocks)


def _build_context(payload: ReportPayload) -> dict[str, str]:
    investigation_id = (
        payload.investigation_id if payload.investigation_id is not None else "unknown"
    )
    return {
        "title": _text(
            f"Incident Report {payload.organization} #{investigation_id}"
        ),
        "investigation_id": _text(investigation_id),
        "timestamp": _text(_timestamp(payload.investigation_time)),
        "organization": _text(payload.organization),
        "server": _text(f"{payload.server_host}:{payload.server_port}"),
        "scope": _text(payload.database_scope),
        "databases": _database_scope(payload),
        "lookback": _text(f"{payload.lookback_hours} hour(s)"),
        "confidence": _text(
            f"{payload.confidence_percent}% ({payload.confidence_label.value})"
        ),
        "severity": _text(payload.severity.value),
        "disclaimer": _text(payload.read_only_disclaimer),
        "sources_table": _sources_table(payload),
        "findings_table": _findings_table(payload),
        "findings_detail": _findings_detail(payload),
        "causes": _causes(payload),
        "gaps": _gaps(payload),
        "recommendations": _recommendations(payload),
    }


def render_html(payload: ReportPayload) -> str:
    """Return the fully-populated, credential-free HTML for ``payload``.

    Kept separate from :func:`render` so the document structure and its
    determinism can be tested without WeasyPrint or system libraries.
    """
    html = TEMPLATE_PATH.read_text(encoding="utf-8")
    for key, value in _build_context(payload).items():
        html = html.replace("{{" + key + "}}", value)
    return html


def render(payload: ReportPayload) -> bytes:
    """Render ``payload`` to PDF bytes with WeasyPrint.

    Raises :class:`PdfRenderError` if WeasyPrint is unavailable or rendering
    fails, so the caller can answer with a clean ``500`` and no traceback.
    """
    if not WEASYPRINT_AVAILABLE or HTML is None:
        raise PdfRenderError(
            f"WeasyPrint is not available: {WEASYPRINT_ERROR}"
        )
    try:
        html = render_html(payload)
        return HTML(string=html).write_pdf()
    except PdfRenderError:
        raise
    except Exception as exc:  # normalize any template or renderer failure
        raise PdfRenderError(f"PDF rendering failed: {type(exc).__name__}") from exc


def report_filename(payload: ReportPayload, investigation_id: int) -> str:
    """Build the ``<organization>-<id>.pdf`` attachment name, header-safe."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", payload.organization).strip("-._")
    return f"{slug or 'report'}-{investigation_id}.pdf"
