"""SQL Server error-log evidence collector (plan.md §4 area 3, #15).

Normalises the raw error-log rows returned by the catalog source
``sql_server_error_log`` (``sys.dm_os_error_log`` / ``sys.xp_readerrorlog``
output) into structured :class:`~app.evidence.models.EvidenceRow` findings: one
row per log entry, carrying the timestamp, a ``high``/``medium``/``low``
severity mapped from the SQL Server severity, the (redacted) error text, and a
``critical`` flag for a severity of 17 or higher.

Severity comes from a dedicated ``severity`` column when the source provides one
and is otherwise parsed from the message text, since the classic
``xp_readerrorlog`` rows embed it as ``Error: 18456, Severity: 14, State: 1``.
The mapping is the SQL Server convention: severity ``>= 17`` is a serious error
(``high``), ``11-16`` a user error (``medium``), and ``0-10`` informational
(``low``). A severity that cannot be determined is reported as ``low``.

The collector only reads rows the #12 engine already fetched; it never executes
SQL and never runs ``xp_readerrorlog`` itself. Error text is passed through the
#5 filter so a connection-string credential log entry never reaches a finding.
Timestamps are normalised to timezone-aware UTC and findings are ordered by
timestamp, never by row order.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from app.evidence.collectors import SourceEvidence, failure_status, value
from app.evidence.models import EvidenceRow, QueryStatus, SourceStatus
from app.evidence.registry import EvidenceRegistry
from app.logredact import redact_text

#: Catalog id of the SQL Server error-log query (#8).
SOURCE_ID = "sql_server_error_log"

#: Issue area every emitted row belongs to.
AREA = "errors"

#: SQL Server severity at or above which an entry is a serious error.
HIGH_SEVERITY = 17

#: SQL Server severity at or above which an entry is a user error.
MEDIUM_SEVERITY = 11

#: Longest error text kept verbatim in a row's details; longer text is truncated.
TEXT_LIMIT = 1000

#: Longest single-line error summary used in the description.
SUMMARY_LIMIT = 200

#: ``Error: 18456, Severity: 14, State: 1`` - the classic error-log shape.
_ERROR_SEVERITY_RE = re.compile(
    r"Error:\s*\d+\s*,\s*Severity:\s*(\d+)", re.IGNORECASE
)

#: A bare ``Severity: 14`` / ``Level 16`` marker.
_SEVERITY_RE = re.compile(r"Severity:\s*(\d+)", re.IGNORECASE)
_LEVEL_RE = re.compile(r"\bLevel\s+(\d+)\b", re.IGNORECASE)


def _as_int(raw: Any) -> int | None:
    """Return ``raw`` as an ``int``, or ``None`` when it is not an integer."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw) if raw.is_integer() else None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _error_text(row: Any) -> str:
    """The raw error text for ``row`` (``error_text`` or ``text``), stripped."""
    raw = value(row, "error_text", None)
    if raw is None:
        raw = value(row, "text", None)
    if raw is None:
        return ""
    return str(raw).strip()


def parse_severity(text: str) -> int | None:
    """Extract the SQL Server severity from ``text``, or ``None``.

    Recognises the embedded ``Error: n, Severity: n`` shape first, then a bare
    ``Severity: n`` or ``Level n`` marker.
    """
    for pattern in (_ERROR_SEVERITY_RE, _SEVERITY_RE, _LEVEL_RE):
        match = pattern.search(text)
        if match:
            return int(match.group(1))
    return None


def _row_severity(row: Any) -> int | None:
    """The severity code for ``row``: the column when present, else the text."""
    column = _as_int(value(row, "severity", None))
    if column is not None:
        return column
    return parse_severity(_error_text(row))


def severity_label(severity: int | None) -> str:
    """Map a SQL Server severity to ``high``/``medium``/``low``.

    ``>= 17`` is high, ``11-16`` is medium, and anything lower (or unknown) is
    low.
    """
    if severity is None:
        return "low"
    if severity >= HIGH_SEVERITY:
        return "high"
    if severity >= MEDIUM_SEVERITY:
        return "medium"
    return "low"


def _is_critical(severity: int | None) -> bool:
    """Whether ``severity`` is a critical (``>= 17``) entry."""
    return severity is not None and severity >= HIGH_SEVERITY


def _parse_datetime(raw: Any) -> datetime | None:
    """Parse an error-log timestamp as timezone-aware UTC, or ``None``.

    Accepts the already-typed ``datetime`` the driver returns and ISO-8601
    strings; a naive value is assumed UTC (SQL Server logs carry no offset).
    """
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=UTC)
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _collapse(text: str, limit: int) -> str:
    """Collapse whitespace to one line and truncate to ``limit`` characters."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit].rstrip() + "..."


def _process_info(row: Any) -> str:
    """The (redacted) ``process_info`` for ``row``, or an empty string."""
    raw = value(row, "process_info", None)
    if raw is None:
        return ""
    return redact_text(str(raw)).strip()


def _finding(row: Any) -> EvidenceRow:
    """Build one :class:`EvidenceRow` for a single error-log entry."""
    raw_text = _error_text(row)
    text = redact_text(raw_text)
    severity = _row_severity(row)
    label = severity_label(severity)
    critical = _is_critical(severity)
    timestamp = _parse_datetime(value(row, "log_date", None))

    summary = _collapse(text, SUMMARY_LIMIT) or "no error text was recorded"
    critical_text = " Critical." if critical else ""
    description = (
        f"SQL Server error log entry (severity {label}{_code_suffix(severity)})"
        f"{_when_suffix(timestamp)}: {summary}.{critical_text}"
    )

    return EvidenceRow(
        area=AREA,
        timestamp=timestamp,
        description=description,
        status=SourceStatus.collected,
        details={
            "severity": label,
            "severity_code": severity,
            "critical": critical,
            "error_text": _collapse(text, TEXT_LIMIT),
            "process_info": _process_info(row),
            "log_date": value(row, "log_date", None),
        },
    )


def _code_suffix(severity: int | None) -> str:
    """``, code 14`` suffix when a numeric severity is known."""
    return f", code {severity}" if severity is not None else ""


def _when_suffix(timestamp: datetime | None) -> str:
    """`` at <iso>`` suffix when a timestamp is known."""
    return f" at {timestamp.isoformat()}" if timestamp is not None else ""


def _sort_key(finding: EvidenceRow) -> tuple[bool, datetime]:
    """Order findings by timestamp ascending, undated findings last."""
    if finding.timestamp is None:
        return (True, datetime.min.replace(tzinfo=UTC))
    return (False, finding.timestamp)


class ErrorLogCollector:
    """One row per error-log entry; zero rows is still ``collected``."""

    source_id = SOURCE_ID

    def collect(
        self, evidence: SourceEvidence, registry: EvidenceRegistry
    ) -> list[EvidenceRow]:
        if evidence.status is not QueryStatus.ok:
            status, note = failure_status(evidence)
            registry.record(self.source_id, status, note)
            return []

        findings = [_finding(row) for row in evidence.rows]
        findings.sort(key=_sort_key)

        critical_count = sum(1 for finding in findings if finding.details["critical"])
        registry.record(
            self.source_id,
            SourceStatus.collected,
            f"{len(findings)} error log entr(ies) found; "
            f"{critical_count} critical.",
        )
        return findings


def collect_errors(
    evidence: SourceEvidence, registry: EvidenceRegistry
) -> list[EvidenceRow]:
    """Run the error-log collector and return its structured rows."""
    return ErrorLogCollector().collect(evidence, registry)
