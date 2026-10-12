"""Persistence, append-only audit trail and 7-day retention (#21).

This module is the single storage layer for the #12/#17/#18/#19 artifacts: raw
:class:`~app.evidence.models.QueryEvidence`, #17
:class:`~app.rules.signals.Signal` findings, the #18
:class:`~app.ai.schemas.Verdict`, and the assembled #19
:class:`~app.report.schemas.ReportPayload`. The Pydantic models are reused
verbatim as the JSON schema, so the stored shape cannot drift from the layers
that produced them.

Retention is a fixed :data:`RETENTION_WINDOW` (7 days, plan.md §7): the
:meth:`PersistenceRepository.cleanup` method deletes rows strictly older than
the window from all five #21 tables and is invoked by the ``retention_cleanup``
arq task in :mod:`app.worker`; the API never deletes on the request path.

Credentials never reach these rows by construction: every JSON value and audit
field passes through :func:`sanitize_json`/:func:`redact_text` (the #5 filter)
before it is written, secret-named keys are masked, and no table has a
credential column. Size caps (:data:`MAX_EVIDENCE_ROWS_PER_SOURCE`,
:data:`MAX_STORED_JSON_BYTES`) keep a single payload from ballooning the
repository database.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import delete, select

from app.ai.schemas import Verdict
from app.db.models import (
    Audit,
    EvidenceRun,
    Finding,
    ReportPayloadRecord,
    VerdictRecord,
)
from app.db.session import session_scope
from app.evidence.models import QueryEvidence
from app.investigations.repository import AuditEntry
from app.logredact import MASK, SECRET_KEYS, redact_text
from app.report.schemas import ReportPayload
from app.rules.signals import Signal

__all__ = [
    "MAX_EVIDENCE_ROWS_PER_SOURCE",
    "MAX_STORED_JSON_BYTES",
    "RETENTION_DAYS",
    "RETENTION_WINDOW",
    "CleanupReport",
    "EvidenceRunRecord",
    "FindingRecord",
    "InMemoryPersistenceRepository",
    "PersistenceRepository",
    "SqlAlchemyAuditWriter",
    "SqlAlchemyPersistenceRepository",
    "cap_evidence_rows",
    "sanitize_json",
]

#: Fixed retention window (plan.md §7). The single point of change for #21;
#: not configurable by design (issue out of scope: "making the retention
#: period configurable").
RETENTION_DAYS = 7
RETENTION_WINDOW = timedelta(days=RETENTION_DAYS)

#: Raw evidence rows kept per (investigation, source); larger results are
#: truncated before storage so one run cannot balloon the repository database.
MAX_EVIDENCE_ROWS_PER_SOURCE = 1000

#: Longest free-text value persisted in a Unicode column; longer values are
#: truncated with a marker. Keeps a description/error inside its column size.
MAX_TEXT_LENGTH = 16384

#: Longest finding description persisted (its column is ``Unicode(2048)``).
MAX_DESCRIPTION_LENGTH = 2048

#: Total bytes a single stored JSON value may occupy before it is replaced by a
#: truncation marker.
MAX_STORED_JSON_BYTES = 2_000_000

#: Suffix appended when a text value is truncated to a cap.
TRUNCATION_MARKER = "...[truncated]"


def _utcnow() -> datetime:
    """Default clock: timezone-aware UTC now."""
    return datetime.now(UTC)


def _is_secret_key(key: object) -> bool:
    """Whether ``key`` names a secret whose value must be masked (#5 filter)."""
    return isinstance(key, str) and key.lower() in SECRET_KEYS


def redact_value(value: Any) -> Any:
    """Recursively mask secret-named keys and secret-shaped strings.

    Reuses the #5 :mod:`app.logredact` vocabulary: a mapping entry whose key is
    ``password``/``pwd``/``secret``/``api_key``/``token`` is replaced wholly
    with :data:`~app.logredact.MASK`, and every other string is passed through
    :func:`~app.logredact.redact_text` so ``password=...``-shaped text is masked
    too.
    """
    if isinstance(value, Mapping):
        return {
            key: (MASK if _is_secret_key(key) else redact_value(item))
            for key, item in value.items()
        }
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, (list, tuple)):
        return [redact_value(item) for item in value]
    return value


def _truncate_text(value: str, *, limit: int = MAX_TEXT_LENGTH) -> str:
    """Truncate ``value`` to ``limit`` chars, appending the marker if cut."""
    if len(value) <= limit:
        return value
    keep = max(limit - len(TRUNCATION_MARKER), 0)
    return value[:keep] + TRUNCATION_MARKER


def _cap_text(value: Any) -> Any:
    """Recursively truncate over-long strings inside ``value``."""
    if isinstance(value, str):
        return _truncate_text(value)
    if isinstance(value, Mapping):
        return {key: _cap_text(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_cap_text(item) for item in value]
    return value


def _cap_json_bytes(value: Any, *, max_bytes: int = MAX_STORED_JSON_BYTES) -> Any:
    """Replace ``value`` with a marker when its JSON encoding exceeds ``max_bytes``."""
    try:
        encoded = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return {"truncated": True, "reason": "value was not JSON serialisable"}
    if len(encoded.encode("utf-8")) <= max_bytes:
        return value
    return {
        "truncated": True,
        "reason": f"stored value exceeded the {max_bytes}-byte cap",
    }


def sanitize_json(value: Any) -> Any:
    """Redact secret-shaped content and enforce the JSON size caps.

    The order is redact -> truncate over-long text -> enforce the byte cap, so a
    secret is removed before it can influence the size decision.
    """
    return _cap_json_bytes(_cap_text(redact_value(value)))


def cap_evidence_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep at most :data:`MAX_EVIDENCE_ROWS_PER_SOURCE` raw evidence rows."""
    return list(rows[:MAX_EVIDENCE_ROWS_PER_SOURCE])


@dataclass(frozen=True)
class EvidenceRunRecord:
    """A stored ``evidence_run`` row, without any credential material."""

    id: int
    investigation_id: int
    source: str
    status: str
    results: list[dict[str, Any]]
    duration: float
    error: str | None
    created_at: datetime


@dataclass(frozen=True)
class FindingRecord:
    """A stored ``finding`` row, mirroring the #17 signal fields."""

    id: int
    investigation_id: int
    area: str
    severity: str
    description: str
    evidence_refs: list[str]
    created_at: datetime


@dataclass(frozen=True)
class CleanupReport:
    """Rows deleted per table by one retention cleanup pass."""

    deleted: dict[str, int]

    @property
    def total(self) -> int:
        """Total rows deleted across every retained table."""
        return sum(self.deleted.values())


#: The five #21 tables the retention cleanup empties, oldest rows first.
_RETENTION_TABLES: tuple[tuple[str, type[Any]], ...] = (
    ("evidence_run", EvidenceRun),
    ("finding", Finding),
    ("verdict", VerdictRecord),
    ("report_payload", ReportPayloadRecord),
    ("audit", Audit),
)


@runtime_checkable
class PersistenceRepository(Protocol):
    """Store/read the #12/#17/#18/#19 artifacts and run retention cleanup."""

    def record_evidence_run(
        self, investigation_id: int, evidence: QueryEvidence
    ) -> None:
        """Persist one raw evidence record."""

    def list_evidence_runs(self, investigation_id: int) -> list[EvidenceRunRecord]:
        """Every stored evidence record for ``investigation_id``, oldest first."""

    def record_finding(self, investigation_id: int, finding: Signal) -> None:
        """Persist one #17 finding (signal)."""

    def list_findings(self, investigation_id: int) -> list[FindingRecord]:
        """Every stored finding for ``investigation_id``, oldest first."""

    def record_verdict(self, investigation_id: int, verdict: Verdict) -> None:
        """Persist one #18 verdict."""

    def get_verdict(self, investigation_id: int) -> Verdict | None:
        """The newest stored verdict for ``investigation_id``, or ``None``."""

    def store_report_payload(
        self,
        investigation_id: int,
        payload: ReportPayload | Mapping[str, Any],
    ) -> None:
        """Store the #19 report payload; a newer one replaces the old row."""

    def get_stored_payload(
        self, investigation_id: int
    ) -> dict[str, Any] | None:
        """The redacted stored payload mapping, or ``None``."""

    def get_report_payload(
        self, investigation_id: int
    ) -> ReportPayload | None:
        """The stored payload re-validated as a :class:`ReportPayload`."""

    def cleanup(self, *, now: datetime | None = None) -> CleanupReport:
        """Delete rows older than the retention window from all five tables."""


def _evidence_run_to_record(model: EvidenceRun) -> EvidenceRunRecord:
    return EvidenceRunRecord(
        id=model.id,
        investigation_id=model.investigation_id,
        source=model.source,
        status=model.status,
        results=list(model.results or []),
        duration=model.duration,
        error=model.error,
        created_at=model.created_at,
    )


def _finding_to_record(model: Finding) -> FindingRecord:
    return FindingRecord(
        id=model.id,
        investigation_id=model.investigation_id,
        area=model.area,
        severity=model.severity,
        description=model.description,
        evidence_refs=list(model.evidence_refs or []),
        created_at=model.created_at,
    )


def _payload_dict(
    payload: ReportPayload | Mapping[str, Any],
) -> dict[str, Any]:
    """Normalize a payload argument to a JSON-ready mapping."""
    if isinstance(payload, Mapping):
        return dict(payload)
    return payload.model_dump(mode="json")


class SqlAlchemyPersistenceRepository:
    """Real, database-backed persistence repository (via #3/#4 sessions)."""

    def __init__(
        self,
        session_factory: Callable[[], Any] | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory or session_scope
        self._clock = clock or _utcnow

    def record_evidence_run(
        self, investigation_id: int, evidence: QueryEvidence
    ) -> None:
        rows = cap_evidence_rows(evidence.rows)
        error = redact_text(evidence.error) if evidence.error else None
        with self._session_factory() as session:
            session.add(
                EvidenceRun(
                    investigation_id=investigation_id,
                    source=evidence.query_id,
                    status=evidence.status.value,
                    results=sanitize_json(rows),
                    duration=evidence.duration_ms,
                    error=(
                        _truncate_text(error, limit=MAX_DESCRIPTION_LENGTH)
                        if error is not None
                        else None
                    ),
                    created_at=self._clock(),
                )
            )

    def list_evidence_runs(
        self, investigation_id: int
    ) -> list[EvidenceRunRecord]:
        with self._session_factory() as session:
            statement = (
                select(EvidenceRun)
                .where(EvidenceRun.investigation_id == investigation_id)
                .order_by(EvidenceRun.id)
            )
            return [
                _evidence_run_to_record(model)
                for model in session.execute(statement).scalars()
            ]

    def record_finding(self, investigation_id: int, finding: Signal) -> None:
        with self._session_factory() as session:
            session.add(
                Finding(
                    investigation_id=investigation_id,
                    area=finding.area,
                    severity=finding.severity.value,
                    description=_truncate_text(
                        redact_text(finding.description),
                        limit=MAX_DESCRIPTION_LENGTH,
                    ),
                    evidence_refs=sanitize_json(list(finding.evidence_ids)),
                    created_at=self._clock(),
                )
            )

    def list_findings(self, investigation_id: int) -> list[FindingRecord]:
        with self._session_factory() as session:
            statement = (
                select(Finding)
                .where(Finding.investigation_id == investigation_id)
                .order_by(Finding.id)
            )
            return [
                _finding_to_record(model)
                for model in session.execute(statement).scalars()
            ]

    def record_verdict(self, investigation_id: int, verdict: Verdict) -> None:
        with self._session_factory() as session:
            session.add(
                VerdictRecord(
                    investigation_id=investigation_id,
                    payload=sanitize_json(verdict.model_dump(mode="json")),
                    created_at=self._clock(),
                )
            )

    def get_verdict(self, investigation_id: int) -> Verdict | None:
        with self._session_factory() as session:
            statement = (
                select(VerdictRecord)
                .where(VerdictRecord.investigation_id == investigation_id)
                .order_by(VerdictRecord.id.desc())
                .limit(1)
            )
            model = session.execute(statement).scalars().first()
            if model is None:
                return None
            return Verdict.model_validate(model.payload)

    def store_report_payload(
        self,
        investigation_id: int,
        payload: ReportPayload | Mapping[str, Any],
    ) -> None:
        data = sanitize_json(_payload_dict(payload))
        now = self._clock()
        with self._session_factory() as session:
            statement = select(ReportPayloadRecord).where(
                ReportPayloadRecord.investigation_id == investigation_id
            )
            existing = session.execute(statement).scalars().first()
            if existing is not None:
                existing.payload = data
                existing.created_at = now
                return
            session.add(
                ReportPayloadRecord(
                    investigation_id=investigation_id,
                    payload=data,
                    created_at=now,
                )
            )

    def get_stored_payload(
        self, investigation_id: int
    ) -> dict[str, Any] | None:
        with self._session_factory() as session:
            statement = select(ReportPayloadRecord).where(
                ReportPayloadRecord.investigation_id == investigation_id
            )
            model = session.execute(statement).scalars().first()
            if model is None:
                return None
            return dict(model.payload or {})

    def get_report_payload(
        self, investigation_id: int
    ) -> ReportPayload | None:
        data = self.get_stored_payload(investigation_id)
        if data is None:
            return None
        return ReportPayload.model_validate(data)

    def cleanup(self, *, now: datetime | None = None) -> CleanupReport:
        cutoff = (now or self._clock()) - RETENTION_WINDOW
        deleted: dict[str, int] = {}
        with self._session_factory() as session:
            for name, model in _RETENTION_TABLES:
                result = session.execute(
                    delete(model).where(model.created_at < cutoff)
                )
                deleted[name] = int(result.rowcount or 0)
        return CleanupReport(deleted=deleted)


class InMemoryPersistenceRepository:
    """In-memory repository for unit tests (no SQL Server required)."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or _utcnow
        self._next_id = 1
        self.evidence_runs: list[EvidenceRunRecord] = []
        self.findings: list[FindingRecord] = []
        self.verdicts: list[tuple[int, int, dict[str, Any], datetime]] = []
        self.payloads: dict[int, tuple[dict[str, Any], datetime]] = {}
        # Audit rows are written through an AuditWriter in production;
        # the in-memory double tracks them so cleanup covers all five
        # #21 tables without a SQL Server.
        self.audit: list[tuple[AuditEntry, datetime]] = []

    def _new_id(self) -> int:
        value = self._next_id
        self._next_id += 1
        return value

    def record_audit(self, entry: AuditEntry) -> None:
        """Append ``entry`` to the in-memory audit trail (#21)."""
        self.audit.append((entry, self._clock()))

    def record_evidence_run(
        self, investigation_id: int, evidence: QueryEvidence
    ) -> None:
        rows = cap_evidence_rows(evidence.rows)
        error = redact_text(evidence.error) if evidence.error else None
        self.evidence_runs.append(
            EvidenceRunRecord(
                id=self._new_id(),
                investigation_id=investigation_id,
                source=evidence.query_id,
                status=evidence.status.value,
                results=sanitize_json(rows),
                duration=evidence.duration_ms,
                error=(
                    _truncate_text(error, limit=MAX_DESCRIPTION_LENGTH)
                    if error is not None
                    else None
                ),
                created_at=self._clock(),
            )
        )

    def list_evidence_runs(
        self, investigation_id: int
    ) -> list[EvidenceRunRecord]:
        return [
            row
            for row in self.evidence_runs
            if row.investigation_id == investigation_id
        ]

    def record_finding(self, investigation_id: int, finding: Signal) -> None:
        self.findings.append(
            FindingRecord(
                id=self._new_id(),
                investigation_id=investigation_id,
                area=finding.area,
                severity=finding.severity.value,
                description=_truncate_text(
                    redact_text(finding.description),
                    limit=MAX_DESCRIPTION_LENGTH,
                ),
                evidence_refs=sanitize_json(list(finding.evidence_ids)),
                created_at=self._clock(),
            )
        )

    def list_findings(self, investigation_id: int) -> list[FindingRecord]:
        return [
            row
            for row in self.findings
            if row.investigation_id == investigation_id
        ]

    def record_verdict(self, investigation_id: int, verdict: Verdict) -> None:
        self.verdicts.append(
            (
                self._new_id(),
                investigation_id,
                sanitize_json(verdict.model_dump(mode="json")),
                self._clock(),
            )
        )

    def get_verdict(self, investigation_id: int) -> Verdict | None:
        matches = [
            row for row in self.verdicts if row[1] == investigation_id
        ]
        if not matches:
            return None
        return Verdict.model_validate(matches[-1][2])

    def store_report_payload(
        self,
        investigation_id: int,
        payload: ReportPayload | Mapping[str, Any],
    ) -> None:
        data = sanitize_json(_payload_dict(payload))
        self.payloads[investigation_id] = (data, self._clock())

    def get_stored_payload(
        self, investigation_id: int
    ) -> dict[str, Any] | None:
        stored = self.payloads.get(investigation_id)
        return dict(stored[0]) if stored is not None else None

    def get_report_payload(
        self, investigation_id: int
    ) -> ReportPayload | None:
        data = self.get_stored_payload(investigation_id)
        if data is None:
            return None
        return ReportPayload.model_validate(data)

    def cleanup(self, *, now: datetime | None = None) -> CleanupReport:
        cutoff = (now or self._clock()) - RETENTION_WINDOW
        deleted: dict[str, int] = {}

        before = len(self.evidence_runs)
        self.evidence_runs = [
            row for row in self.evidence_runs if row.created_at >= cutoff
        ]
        deleted["evidence_run"] = before - len(self.evidence_runs)

        before = len(self.findings)
        self.findings = [
            row for row in self.findings if row.created_at >= cutoff
        ]
        deleted["finding"] = before - len(self.findings)

        before = len(self.verdicts)
        self.verdicts = [
            row for row in self.verdicts if row[3] >= cutoff
        ]
        deleted["verdict"] = before - len(self.verdicts)

        before = len(self.payloads)
        self.payloads = {
            key: value
            for key, value in self.payloads.items()
            if value[1] >= cutoff
        }
        deleted["report_payload"] = before - len(self.payloads)

        before = len(self.audit)
        self.audit = [row for row in self.audit if row[1] >= cutoff]
        deleted["audit"] = before - len(self.audit)

        return CleanupReport(deleted=deleted)


class SqlAlchemyAuditWriter:
    """Real append-only audit writer (#21) backed by the ``audit`` table.

    Accepts the existing #9/#10 :class:`~app.investigations.repository.AuditEntry`
    shape. Its ``organization_id`` is not stored: each organization has its own
    database, so the organization is implicit. ``actor``, ``action`` and
    ``target`` are redacted before insert so no secret can reach the trail.
    """

    def __init__(
        self,
        session_factory: Callable[[], Any] | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory or session_scope
        self._clock = clock or _utcnow

    def write(self, entry: AuditEntry) -> None:
        with self._session_factory() as session:
            session.add(
                Audit(
                    actor=_truncate_text(redact_text(entry.actor), limit=255),
                    action=_truncate_text(redact_text(entry.action), limit=64),
                    target=_truncate_text(
                        redact_text(entry.target), limit=512
                    ),
                    created_at=self._clock(),
                )
            )
