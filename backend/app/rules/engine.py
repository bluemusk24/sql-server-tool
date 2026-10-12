"""Built-in rules engine (#17).

:func:`evaluate` turns the normalized evidence rows from the #13-#16 collectors
into a stable list of :class:`~app.rules.signals.Signal` objects, one rule module
per issue area. It is a *pure* function of its inputs:

* no I/O, no global state, and no external AI -- the rules are deterministic
  Python in :mod:`app.rules`;
* it only ever sees the current investigation's rows, never another run's, so
  there is no cross-investigation baseline (plan.md §4/§5);
* it assigns each row a deterministic, content-derived **emitted id** and a
  signal references the rows it used through ``Signal.evidence_ids``.

A row is only usable when it is real evidence: gap markers (``available=False``)
and rows whose area's source status is ``unavailable-*`` never fire a rule. The
absence of evidence is a coverage gap for #19, not a signal (#17 constraint).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence

from app.evidence import availability as availability_evidence
from app.evidence import blocking as blocking_evidence
from app.evidence import deadlocks as deadlocks_evidence
from app.evidence import errors as errors_evidence
from app.evidence import jobs as jobs_evidence
from app.evidence import queries as queries_evidence
from app.evidence import storage as storage_evidence
from app.evidence.models import EvidenceRow, EvidenceSource, SourceStatus
from app.rules import availability, blocking, deadlocks, errors, jobs, queries, storage
from app.rules.signals import IndexedEvidence, Signal

#: Rule modules in the fixed evaluation order (the #17/"plan.md §2" area order).
RULES = (blocking, deadlocks, queries, jobs, errors, availability, storage)

#: The issue areas this engine evaluates, in output order.
AREA_ORDER = tuple(module.AREA for module in RULES)

#: Source id -> issue area, so a status map keyed by source id is understood too.
SOURCE_TO_AREA: dict[str, str] = {
    blocking_evidence.CURRENT_SOURCE_ID: blocking.AREA,
    blocking_evidence.HISTORICAL_SOURCE_ID: blocking.AREA,
    deadlocks_evidence.SOURCE_ID: deadlocks.AREA,
    queries_evidence.SOURCE_ID: queries.AREA,
    jobs_evidence.SOURCE_ID: jobs.AREA,
    errors_evidence.SOURCE_ID: errors.AREA,
    availability_evidence.SOURCE_ID: availability.AREA,
    storage_evidence.SOURCE_ID: storage.AREA,
}

#: Statuses that mean a source's rows must never be used to fire a rule.
UNAVAILABLE_STATUSES = frozenset(
    {
        SourceStatus.unavailable_permission,
        SourceStatus.unavailable_connection,
        SourceStatus.unavailable_setting,
    }
)


def evidence_id(row: EvidenceRow) -> str:
    """The deterministic id emitted for ``row``.

    Derived from the row's content (area, availability, status, timestamp,
    description, and details), so it does not depend on the row's position in
    the input. Identical rows therefore share an id and are de-duplicated by the
    engine, and adding an unrelated row cannot change another row's id.
    """
    status = row.status.value if isinstance(row.status, SourceStatus) else str(row.status)
    payload = {
        "area": row.area,
        "available": row.available,
        "status": status,
        "timestamp": row.timestamp.isoformat() if row.timestamp is not None else None,
        "description": row.description,
        "details": row.details,
    }
    canonical = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
    return f"{row.area}-{digest}"


def index_evidence(rows: Iterable[EvidenceRow]) -> list[IndexedEvidence]:
    """Pair every row with its emitted id, de-duplicating identical rows.

    Preserves first-seen order, so the returned list is a stable view of the
    current investigation's evidence that callers (#19/#26) can map a
    ``Signal.evidence_ids`` entry back to.
    """
    indexed: list[IndexedEvidence] = []
    seen: set[str] = set()
    for row in rows:
        emitted = evidence_id(row)
        if emitted in seen:
            continue
        seen.add(emitted)
        indexed.append(IndexedEvidence(emitted, row))
    return indexed


def area_statuses(sources: Sequence[EvidenceSource]) -> dict[str, SourceStatus]:
    """Collapse an evidence inventory into one status per issue area.

    ``collected`` wins whenever any source of an area was collected; otherwise
    the first status seen is kept. Areas with no source are absent.
    """
    statuses: dict[str, SourceStatus] = {}
    for source in sources:
        current = statuses.get(source.issue_area)
        if current is None or source.status is SourceStatus.collected:
            statuses[source.issue_area] = source.status
    return statuses


def _normalize_statuses(
    source_statuses: Mapping[str, SourceStatus] | Sequence[EvidenceSource] | None,
) -> dict[str, SourceStatus]:
    """Normalize the ``source_statuses`` argument into an area -> status map.

    Accepts a mapping keyed by issue area or by source id, or a sequence of
    #12 :class:`~app.evidence.models.EvidenceSource` records. Unknown keys are
    ignored; with no argument no area is treated as unavailable.
    """
    if source_statuses is None:
        return {}
    if isinstance(source_statuses, Mapping):
        statuses: dict[str, SourceStatus] = {}
        for key, status in source_statuses.items():
            area = key if key in AREA_ORDER else SOURCE_TO_AREA.get(key)
            if area is None:
                continue
            current = statuses.get(area)
            if current is None or status is SourceStatus.collected:
                statuses[area] = status
        return statuses
    return area_statuses(list(source_statuses))


def _is_unavailable(area: str, statuses: Mapping[str, SourceStatus]) -> bool:
    """Whether ``area``'s source status forbids firing a rule."""
    return statuses.get(area) in UNAVAILABLE_STATUSES


def evaluate(
    evidence_rows: Iterable[EvidenceRow],
    source_statuses: Mapping[str, SourceStatus] | Sequence[EvidenceSource] | None = None,
) -> list[Signal]:
    """Evaluate ``evidence_rows`` into a stable list of signals.

    Rows are indexed with their emitted ids and de-duplicated by content. A row
    is skipped when it is a gap marker (``available=False``), when its own status
    is ``unavailable-*``, or when its area's status in ``source_statuses`` is
    ``unavailable-*``. Signals are ordered by the fixed rule order and then by
    the row order within each rule.
    """
    statuses = _normalize_statuses(source_statuses)
    by_area: dict[str, list[IndexedEvidence]] = {}
    for item in index_evidence(evidence_rows):
        row = item.row
        if not row.available or row.status in UNAVAILABLE_STATUSES:
            continue
        if _is_unavailable(row.area, statuses):
            continue
        by_area.setdefault(row.area, []).append(item)

    signals: list[Signal] = []
    for module in RULES:
        signals.extend(module.evaluate(by_area.get(module.AREA, [])))
    return signals
