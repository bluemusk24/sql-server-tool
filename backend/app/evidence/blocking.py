"""Blocking evidence collector (plan.md §4 area 1, #13).

Normalises the #12 raw rows of the catalog source ``blocking_current_chains``
into one structured :class:`~app.evidence.models.EvidenceRow` per blocked
session, and emits an explicit *gap* marker for historical blocking because
SQL Server retains none by default (the read-only tool never creates an
extended-events session, plan.md §4).

Current blocking is a *snapshot*: it is ``collected`` even with zero rows (a
healthy server simply had no blocking at the moment of the run). Historical
blocking is a *gap*: it is ``available=False`` with the exact note
:data:`NO_HISTORY_NOTE` whenever the server retained no history, so the report
can mark the gap without claiming the source was checked.
"""

from __future__ import annotations

from typing import Any

from app.evidence.collectors import (
    SourceEvidence,
    failure_status,
    value,
)
from app.evidence.models import EvidenceRow, QueryStatus, SourceStatus
from app.evidence.registry import EvidenceRegistry

#: Catalog id of the current-blocking DMV query (#8).
CURRENT_SOURCE_ID = "blocking_current_chains"

#: Synthetic source id for historical blocking (no catalog query exists; the
#: server retains no history by default and the tool never creates sessions).
HISTORICAL_SOURCE_ID = "blocking_history"

#: Exact note the report keys on to show the historical-blocking gap.
NO_HISTORY_NOTE = "no retained historical blocking evidence"

#: Guard against a malformed cyclic chain when resolving the root blocker.
_MAX_CHAIN_DEPTH = 1000


def _root_blocker(
    blocker: Any,
    blocking_of: dict[Any, Any],
    blocked_ids: set[Any],
) -> Any:
    """Follow the block chain from ``blocker`` to the session that holds root.

    ``blocking_of`` maps a blocked session id to the session blocking it; a
    session whose id is not in ``blocked_ids`` is itself not blocked and is the
    root. Cycles (which cannot occur live) are cut at
    :data:`_MAX_CHAIN_DEPTH`.
    """
    current = blocker
    for _ in range(_MAX_CHAIN_DEPTH):
        if current not in blocked_ids:
            break
        nxt = blocking_of.get(current)
        if nxt is None:
            break
        current = nxt
    return current


def _duration_text(duration_ms: Any) -> str:
    if duration_ms is None:
        return "an unknown duration"
    return f"{duration_ms} ms"


class CurrentBlockingCollector:
    """One row per currently blocked session; zero rows is still ``collected``."""

    source_id = CURRENT_SOURCE_ID

    def collect(
        self, evidence: SourceEvidence, registry: EvidenceRegistry
    ) -> list[EvidenceRow]:
        if evidence.status is not QueryStatus.ok:
            status, note = failure_status(evidence)
            registry.record(self.source_id, status, note)
            return []

        rows = list(evidence.rows)
        blocked_ids = {value(row, "session_id") for row in rows}
        blocking_of = {
            value(row, "session_id"): value(row, "blocking_session_id")
            for row in rows
        }

        findings: list[EvidenceRow] = []
        for row in rows:
            blocked = value(row, "session_id")
            blocker = value(row, "blocking_session_id")
            duration_ms = value(row, "wait_time")
            wait_type = value(row, "wait_type")
            root = _root_blocker(blocker, blocking_of, blocked_ids)
            wait_text = f" (wait {wait_type})" if wait_type else ""
            findings.append(
                EvidenceRow(
                    area="blocking",
                    timestamp=None,
                    description=(
                        f"Session {blocked} is blocked by session {blocker} "
                        f"for {_duration_text(duration_ms)}{wait_text}."
                    ),
                    status=SourceStatus.collected,
                    details={
                        "blocked_session_id": blocked,
                        "blocking_session_id": blocker,
                        "root_blocker_session_id": root,
                        "duration_ms": duration_ms,
                        "wait_type": wait_type,
                        "login_name": value(row, "login_name"),
                        "session_status": value(row, "status"),
                        "command": value(row, "command"),
                    },
                )
            )

        registry.record(
            self.source_id,
            SourceStatus.collected,
            f"{len(findings)} currently blocked session(s) found.",
        )
        return findings


class HistoricalBlockingCollector:
    """Emits a gap marker unless the server actually retained history."""

    source_id = HISTORICAL_SOURCE_ID

    def collect(
        self, evidence: SourceEvidence, registry: EvidenceRegistry
    ) -> list[EvidenceRow]:
        rows = list(evidence.rows)
        if not rows:
            registry.record(self.source_id, SourceStatus.not_applicable, NO_HISTORY_NOTE)
            return [
                EvidenceRow(
                    area="blocking",
                    timestamp=None,
                    description=(
                        "Historical blocking was not checked: "
                        f"{NO_HISTORY_NOTE}."
                    ),
                    available=False,
                    status=SourceStatus.not_applicable,
                    details={"note": NO_HISTORY_NOTE},
                )
            ]

        findings: list[EvidenceRow] = []
        for row in rows:
            blocked = value(row, "session_id")
            blocker = value(row, "blocking_session_id")
            duration_ms = value(row, "wait_time")
            findings.append(
                EvidenceRow(
                    area="blocking",
                    timestamp=value(row, "event_time"),
                    description=(
                        f"Historical blocking: session {blocked} was blocked by "
                        f"session {blocker} for {_duration_text(duration_ms)}."
                    ),
                    status=SourceStatus.collected,
                    details={
                        "blocked_session_id": blocked,
                        "blocking_session_id": blocker,
                        "duration_ms": duration_ms,
                        "wait_type": value(row, "wait_type"),
                    },
                )
            )
        registry.record(
            self.source_id,
            SourceStatus.collected,
            f"{len(findings)} retained historical blocking event(s) found.",
        )
        return findings


def collect_blocking(
    current: SourceEvidence,
    registry: EvidenceRegistry,
    *,
    historical: SourceEvidence | None = None,
) -> list[EvidenceRow]:
    """Run both blocking collectors and return their combined structured rows.

    ``historical`` defaults to an empty :class:`SourceEvidence` for
    :data:`HISTORICAL_SOURCE_ID`, which yields the gap marker.
    """
    rows = CurrentBlockingCollector().collect(current, registry)
    if historical is None:
        historical = SourceEvidence(source_id=HISTORICAL_SOURCE_ID)
    rows.extend(HistoricalBlockingCollector().collect(historical, registry))
    return rows
