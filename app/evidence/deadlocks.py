"""Deadlock evidence collector (plan.md §4 area 1, #13).

Parses the retained ``system_health`` extended-events ring buffer returned by
the catalog source ``deadlock_system_health`` and emits one structured
:class:`~app.evidence.models.EvidenceRow` per deadlock graph (timestamp, victim
session, and participants). A server that returned ring-buffer rows but no
deadlock graph is ``collected`` with zero rows; a server that returned no
ring-buffer rows at all has no retained evidence and is ``not-applicable``.

The XML is parsed with the standard library only (no new dependency) and the
collector never executes SQL or changes a session -- it only reads what the #12
engine already fetched, per plan.md §4.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any

from app.evidence.collectors import SourceEvidence, failure_status, value
from app.evidence.models import EvidenceRow, QueryStatus, SourceStatus
from app.evidence.registry import EvidenceRegistry

#: Catalog id of the system_health deadlock ring-buffer query (#8).
SOURCE_ID = "deadlock_system_health"

#: Name of the extended-events event that carries a deadlock graph.
_DEADLOCK_EVENT = "xml_deadlock_report"


def _localname(tag: Any) -> str:
    """Return an element's tag without any ``{namespace}`` prefix."""
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _parse_timestamp(raw: Any) -> datetime | None:
    """Parse an event/ring timestamp into a :class:`datetime`, or ``None``.

    Accepts the already-typed ``datetime`` the query returns and the ISO-8601
    strings found in deadlock XML; anything unparseable becomes ``None`` rather
    than raising.
    """
    if isinstance(raw, datetime):
        return raw
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _as_int(value: Any) -> Any:
    """Return ``value`` as an ``int`` when it is a numeric string, else as-is."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _process_participants(deadlock: ET.Element) -> list[dict[str, Any]]:
    participants: list[dict[str, Any]] = []
    for element in deadlock.iter():
        if _localname(element.tag) == "process":
            participants.append(
                {
                    "process_id": element.get("id"),
                    "spid": _as_int(element.get("spid")),
                }
            )
    return participants


def _victim_spid(victim_pid: Any, participants: list[dict[str, Any]]) -> Any:
    for participant in participants:
        if participant["process_id"] == victim_pid:
            return participant["spid"]
    return _as_int(victim_pid)


def _deadlock_row(deadlock: ET.Element, timestamp: Any) -> EvidenceRow:
    victim_pid: Any = None
    for element in deadlock.iter():
        if _localname(element.tag) == "victimProcess":
            victim_pid = element.get("id")
            break

    participants = _process_participants(deadlock)
    victim = _victim_spid(victim_pid, participants)
    parsed = _parse_timestamp(timestamp)
    return EvidenceRow(
        area="deadlocks",
        timestamp=parsed,
        description=(
            f"Deadlock with victim session {victim} and "
            f"{len(participants)} participant(s)."
        ),
        status=SourceStatus.collected,
        details={
            "victim": victim,
            "victim_process_id": victim_pid,
            "participants": participants,
        },
    )


def parse_deadlock_graphs(
    xml_text: Any, fallback_timestamp: Any = None
) -> list[EvidenceRow]:
    """Parse zero or more deadlock graphs from one ring-buffer XML value.

    Finds every ``xml_deadlock_report`` event in the ring buffer (or a bare
    ``<deadlock>`` element) and returns one row per graph. Empty, malformed, or
    non-deadlock XML returns an empty list -- never an exception.
    """
    if xml_text is None:
        return []
    text = xml_text if isinstance(xml_text, str) else str(xml_text)
    if not text.strip():
        return []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []

    found: list[tuple[ET.Element, Any]] = []
    events = [
        element
        for element in root.iter()
        if _localname(element.tag) == "event"
        and (element.get("name") or "").lower() == _DEADLOCK_EVENT
    ]
    if events:
        for event in events:
            for deadlock in event.iter():
                if _localname(deadlock.tag) == "deadlock":
                    found.append((deadlock, event.get("timestamp") or fallback_timestamp))
    else:
        for deadlock in root.iter():
            if _localname(deadlock.tag) == "deadlock":
                found.append((deadlock, fallback_timestamp))

    return [_deadlock_row(deadlock, timestamp) for deadlock, timestamp in found]


class DeadlockCollector:
    """One row per retained deadlock; zero rows stays ``collected``."""

    source_id = SOURCE_ID

    def collect(
        self, evidence: SourceEvidence, registry: EvidenceRegistry
    ) -> list[EvidenceRow]:
        if evidence.status is not QueryStatus.ok:
            status, note = failure_status(evidence)
            registry.record(self.source_id, status, note)
            return []

        rows = list(evidence.rows)
        if not rows:
            registry.record(
                self.source_id,
                SourceStatus.not_applicable,
                "server retained no system_health ring buffer evidence",
            )
            return []

        findings: list[EvidenceRow] = []
        for row in rows:
            findings.extend(
                parse_deadlock_graphs(
                    value(row, "ring_data"),
                    value(row, "ring_timestamp"),
                )
            )

        registry.record(
            self.source_id,
            SourceStatus.collected,
            f"{len(findings)} deadlock(s) found in retained evidence.",
        )
        return findings


def collect_deadlocks(
    evidence: SourceEvidence, registry: EvidenceRegistry
) -> list[EvidenceRow]:
    """Run the deadlock collector and return its structured rows."""
    return DeadlockCollector().collect(evidence, registry)
