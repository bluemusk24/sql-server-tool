"""The evidence-source inventory registry (#12).

One :class:`EvidenceSource` per catalog id is tracked as the run proceeds.
Collectors (#13–#16) and the execution engine both update the registry, which
is returned with the run results (and later persisted by #21). The registry is
the single place a summary must consult before claiming a source was checked:
``collected`` is the only status that means evidence was actually gathered.

Applicability predicates decide where a successful but empty source is
``not-applicable`` rather than ``collected`` (for example, historical deadlock
evidence when the server retained no deadlock graphs). A predicate is keyed by
query id and returns ``True`` when the source applies to the server given the
rows that came back.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from app.diagnostics.catalog import Catalog, DiagnosticQuery
from app.evidence.models import EvidenceSource, SourceStatus

#: Statuses ordered by precedence when a source is updated more than once.
#: ``collected`` is strongest; ``not_applicable`` weakest. A source that has not
#: been touched yet accepts the first status it is given, regardless of rank.
_RANK: dict[SourceStatus, int] = {
    SourceStatus.collected: 5,
    SourceStatus.unavailable_permission: 4,
    SourceStatus.unavailable_connection: 3,
    SourceStatus.unavailable_setting: 2,
    SourceStatus.not_applicable: 1,
}

#: A predicate keyed by query id: ``(query, rows) -> applicable?``.
SourceApplicability = Callable[[DiagnosticQuery, Sequence[Any]], bool]


def _has_retained_rows(query: DiagnosticQuery, rows: Sequence[Any]) -> bool:
    """Historical evidence applies only when the server retained some rows.

    Used for the ``system_health`` deadlock ring buffer: no retained rows means
    the source is not applicable on this server, not that it was checked.
    """
    del query
    return len(rows) > 0


#: Per-source applicability predicates. A query id not listed always applies.
APPLICABILITY_PREDICATES: dict[str, SourceApplicability] = {
    "deadlock_system_health": _has_retained_rows,
}


def is_applicable(query: DiagnosticQuery, rows: Sequence[Any]) -> bool:
    """Return whether ``query`` applies to the server given its result ``rows``."""
    predicate = APPLICABILITY_PREDICATES.get(query.id)
    if predicate is None:
        return True
    return predicate(query, rows)


class EvidenceRegistry:
    """Mutable inventory of evidence sources, one per catalog id.

    Constructed from the catalog (so every source is present even if the run
    never reaches it) and updated as the run proceeds. ``all`` returns the
    sources in catalog order for a stable payload.
    """

    def __init__(self, catalog: Catalog) -> None:
        self._order: list[str] = [query.id for query in catalog.all()]
        self._touched: set[str] = set()
        self._sources: dict[str, EvidenceSource] = {
            query.id: EvidenceSource(
                query_id=query.id,
                title=query.title,
                category=query.category,
                issue_area=query.issue_area,
                status=SourceStatus.unavailable_setting,
                note="not collected during this run",
            )
            for query in catalog.all()
        }

    def record(
        self, query_id: str, status: SourceStatus, note: str = ""
    ) -> EvidenceSource:
        """Set the status and note for ``query_id``, creating it if unknown."""
        source = self._sources.get(query_id)
        if source is None:
            source = EvidenceSource(
                query_id=query_id,
                title=query_id,
                category="",
                issue_area="",
                status=status,
                note=note,
            )
            self._order.append(query_id)
            self._sources[query_id] = source
        else:
            source.status = status
            source.note = note
        self._touched.add(query_id)
        return source

    def merge(self, query_id: str, status: SourceStatus, note: str = "") -> EvidenceSource:
        """Update ``query_id`` only if ``status`` outranks its current status.

        Keeps ``collected`` from being downgraded by a later per-database
        failure. An untouched source accepts the first status it is given, so a
        restored default never masks a real (even weak) result.
        """
        current = self._sources.get(query_id)
        if current is None or query_id not in self._touched:
            return self.record(query_id, status, note)
        if _RANK[status] > _RANK[current.status]:
            return self.record(query_id, status, note)
        return current

    def get(self, query_id: str) -> EvidenceSource | None:
        """Return the source for ``query_id``, or ``None`` if it is unknown."""
        return self._sources.get(query_id)

    def all(self) -> list[EvidenceSource]:
        """Every source, in catalog order."""
        return [self._sources[query_id] for query_id in self._order]

    def statuses(self) -> list[SourceStatus]:
        """The statuses of every source, in catalog order."""
        return [source.status for source in self.all()]

    def __len__(self) -> int:
        return len(self._order)
