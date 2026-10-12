"""Storage-pressure evidence collector (plan.md §4 area 4, #16).

Normalises the raw ``sys.master_files`` / ``sys.dm_os_volume_stats`` rows
returned by the catalog source ``database_volume_space``
(:mod:`app.diagnostics.queries.availability`) into structured
:class:`~app.evidence.models.EvidenceRow` findings. The initial indicator is
*low free space*: a file whose free space is under :data:`LOW_FREE_SPACE_RATIO`
(10%) of its allocated size yields one row carrying the file's free percentage.
Files at or above the threshold yield nothing and the source stays
``collected``.

The module is built around a deliberately small :class:`StorageCollector`
contract -- ``collect(database) -> list[StorageIndicator]`` -- so further
indicators (file growth, IO stall, plan.md §10) can be added later by writing
another implementation and appending it to the driver's ``indicators`` list,
without the driver (the caller) changing. Each indicator returns
:class:`StorageIndicator` values, which the driver turns into the shared
:class:`~app.evidence.models.EvidenceRow` shape.

The collector only reads rows the #12 engine already fetched; it never executes
SQL and never extends a file (plan.md §6 - read-only, no remediation). A failed
source query yields zero rows and an ``unavailable-*`` status, never
``collected``. Results are capped per scope at :data:`MAX_STORAGE_ROWS`, keeping
the rows closest to exhaustion first.

Scope follows #9: ``databases=None`` evaluates the whole instance; an explicit
list restricts evaluation to those databases (matched case-insensitively) and
silently ignores the rest.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.evidence.collectors import SourceEvidence, failure_status, value
from app.evidence.models import EvidenceRow, QueryStatus, SourceStatus
from app.evidence.registry import EvidenceRegistry

#: Catalog id of the database volume free-space query (#8).
SOURCE_ID = "database_volume_space"

#: Issue area every emitted row belongs to.
AREA = "storage"

#: A data/log file is low on space when its free fraction is *strictly* below
#: this ratio (10%). Exactly 10.0% free is not flagged; 9.9% is.
LOW_FREE_SPACE_RATIO = 0.10

#: Cap on the storage rows emitted for one investigation scope (documented
#: constant; the groomed spec deliberately leaves large-file pagination out of
#: scope). Rows are ordered most-severe-first before the cap is applied.
MAX_STORAGE_ROWS = 500


def _as_number(raw: Any) -> float | None:
    """Return ``raw`` as a ``float``, or ``None`` when it is not numeric.

    Accepts the driver's numeric types (``int``/``float``/``Decimal``) and
    numeric strings; booleans are rejected.
    """
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return None


def free_ratio(row: Any) -> float | None:
    """The free-space fraction (0..1) of a file row, or ``None`` if unknown.

    Prefers ``available_bytes / total_bytes`` when both are present and the
    total is positive (the unrounded truth); otherwise falls back to the
    server-computed ``free_percent`` column divided by 100.
    """
    available = _as_number(value(row, "available_bytes", None))
    total = _as_number(value(row, "total_bytes", None))
    if available is not None and total is not None and total > 0:
        return available / total
    percent = _as_number(value(row, "free_percent", None))
    if percent is None or percent < 0:
        return None
    return percent / 100.0


def _file_name(row: Any) -> str:
    """The physical file path for ``row``, or ``"unknown"`` when unavailable."""
    raw = value(row, "physical_name", None)
    if raw is None:
        return "unknown"
    text = str(raw).strip()
    return text or "unknown"


def _database_name(row: Any) -> str:
    """The database name for ``row``, or ``"unknown"`` when unavailable."""
    raw = value(row, "database_name", None)
    if raw is None:
        return "unknown"
    text = str(raw).strip()
    return text or "unknown"


def _in_scope(name: str, databases: Sequence[str] | None) -> bool:
    """Whether ``name`` is inside the #9 scope (``None`` is the whole instance)."""
    if databases is None:
        return True
    wanted = {candidate.casefold() for candidate in databases}
    return name.casefold() in wanted


@dataclass(frozen=True)
class DatabaseFiles:
    """One database's file rows, the unit a :class:`StorageCollector` inspects."""

    name: str
    files: Sequence[Any] = ()


@dataclass(frozen=True)
class StorageIndicator:
    """One storage-pressure finding produced by a :class:`StorageCollector`.

    ``rank`` orders findings most-severe-first (lower is worse) before the cap
    is applied. :meth:`to_evidence_row` maps it onto the shared #12 shape.
    """

    database: str
    kind: str
    description: str
    details: dict[str, Any] = field(default_factory=dict)
    rank: float = 1.0

    def to_evidence_row(self) -> EvidenceRow:
        """Return the shared :class:`EvidenceRow` for this indicator."""
        details = dict(self.details)
        details.setdefault("kind", self.kind)
        return EvidenceRow(
            area=AREA,
            timestamp=None,
            description=self.description,
            status=SourceStatus.collected,
            details=details,
        )


@runtime_checkable
class StorageCollector(Protocol):
    """A single storage indicator over one database's files.

    Implementations return zero or more :class:`StorageIndicator` values for a
    database. New indicators implement this protocol and are appended to
    :class:`DatabaseVolumeCollector`'s ``indicators``; the driver never changes.
    """

    #: Stable indicator name (e.g. ``"low_free_space"``).
    kind: str

    def collect(self, database: DatabaseFiles) -> list[StorageIndicator]:
        """Return the storage indicators ``database`` triggers."""


class LowFreeSpaceCollector:
    """Flag each file whose free space is below :data:`LOW_FREE_SPACE_RATIO`."""

    kind = "low_free_space"

    def collect(self, database: DatabaseFiles) -> list[StorageIndicator]:
        findings: list[StorageIndicator] = []
        for row in database.files:
            ratio = free_ratio(row)
            if ratio is None or ratio >= LOW_FREE_SPACE_RATIO:
                continue
            percent = round(ratio * 100.0, 2)
            path = _file_name(row)
            findings.append(
                StorageIndicator(
                    database=database.name,
                    kind=self.kind,
                    description=(
                        f"Database '{database.name}' file '{path}' has "
                        f"{percent}% free space, below the "
                        f"{LOW_FREE_SPACE_RATIO:.0%} threshold."
                    ),
                    rank=ratio,
                    details={
                        "database_name": database.name,
                        "kind": self.kind,
                        "physical_name": path,
                        "logical_volume_name": value(row, "logical_volume_name", None),
                        "free_percent": percent,
                        "free_ratio": ratio,
                        "total_bytes": _as_number(value(row, "total_bytes", None)),
                        "available_bytes": _as_number(
                            value(row, "available_bytes", None)
                        ),
                    },
                )
            )
        return findings


#: The storage indicators the driver runs, in evaluation order.
DEFAULT_INDICATORS: tuple[StorageCollector, ...] = (LowFreeSpaceCollector(),)


def _group_files(
    rows: Sequence[Any],
    databases: Sequence[str] | None,
) -> list[DatabaseFiles]:
    """Group ``rows`` by database name, preserving first-seen order.

    Rows outside the #9 scope are dropped with no group (and therefore no
    per-database rows).
    """
    groups: dict[str, list[Any]] = {}
    order: list[str] = []
    for row in rows:
        name = _database_name(row)
        if not _in_scope(name, databases):
            continue
        if name not in groups:
            groups[name] = []
            order.append(name)
        groups[name].append(row)
    return [DatabaseFiles(name=name, files=groups[name]) for name in order]


def _sort_key(indicator: StorageIndicator) -> tuple[float, str, str, str]:
    """Order findings most-severe-first, then deterministically."""
    return (indicator.rank, indicator.database, indicator.kind, indicator.description)


class DatabaseVolumeCollector:
    """Driver: groups file rows per database and runs each storage indicator.

    ``databases`` is the #9 scope (``None`` for the whole instance).
    ``indicators`` defaults to :data:`DEFAULT_INDICATORS`; passing a different
    sequence is how additional indicators are wired in later without editing
    this class (the extension point the groomed spec asks for).
    """

    source_id = SOURCE_ID

    def __init__(
        self,
        *,
        databases: Sequence[str] | None = None,
        indicators: Sequence[StorageCollector] | None = None,
        max_rows: int = MAX_STORAGE_ROWS,
    ) -> None:
        self._databases = list(databases) if databases is not None else None
        self._indicators = (
            tuple(indicators) if indicators is not None else DEFAULT_INDICATORS
        )
        self._max_rows = max(0, int(max_rows))

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
                "server returned no volume statistics evidence",
            )
            return []

        groups = _group_files(rows, self._databases)
        indicators: list[StorageIndicator] = []
        for database in groups:
            for indicator in self._indicators:
                indicators.extend(indicator.collect(database))

        indicators.sort(key=_sort_key)
        findings = [
            indicator.to_evidence_row() for indicator in indicators[: self._max_rows]
        ]

        registry.record(
            self.source_id,
            SourceStatus.collected,
            f"{len(findings)} storage pressure finding(s) across "
            f"{len(groups)} database(s).",
        )
        return findings


def collect_storage(
    evidence: SourceEvidence,
    registry: EvidenceRegistry,
    *,
    databases: Sequence[str] | None = None,
) -> list[EvidenceRow]:
    """Run the database volume collector and return its structured rows."""
    return DatabaseVolumeCollector(databases=databases).collect(evidence, registry)
