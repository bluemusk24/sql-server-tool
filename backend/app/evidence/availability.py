"""Database availability evidence collector (plan.md §4 area 4, #16).

Normalises the raw ``sys.databases`` rows returned by the catalog source
``non_online_databases`` (:mod:`app.diagnostics.queries.availability`) into one
structured :class:`~app.evidence.models.EvidenceRow` per database that is *not*
in the normal ``ONLINE`` state -- ``offline``, ``suspect``, ``recovering``,
``in_recovery``, ``restoring`` and ``recovery_pending`` per the #16 groomed
spec, plus the other non-normal ``sys.databases`` states (``standby``,
``emergency``, ``copying``) the same source can return. Accessible databases
produce no rows and the source stays ``collected``; a healthy server is not a
gap, it is evidence that nothing was wrong.

The collector only reads rows the #12 engine already fetched; it never executes
SQL, never changes a database state, and never performs remediation (plan.md
§6). A failed source query yields zero rows and an ``unavailable-*`` status with
the credential-free note, never ``collected``.

Scope: the #9 investigation covers either the whole instance or an explicit list
of databases. ``databases=None`` evaluates every row; a list restricts the
collector to those databases (matched case-insensitively, as SQL Server
identifiers depend on collation) and silently ignores the rest, so a
whole-instance source result can never leak a finding for an out-of-scope
database.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.evidence.collectors import SourceEvidence, failure_status, value
from app.evidence.models import EvidenceRow, QueryStatus, SourceStatus
from app.evidence.registry import EvidenceRegistry

#: Catalog id of the non-ONLINE database state query (#8).
SOURCE_ID = "non_online_databases"

#: Issue area every emitted row belongs to.
AREA = "availability"

#: Normalised state names that mean a database is not normally available.
#:
#: The first six are the #16 groomed spec's required set; the remainder are the
#: other non-normal ``sys.databases`` states the same source can surface
#: (``standby`` includes a database with ``is_in_standby = 1``).
NON_NORMAL_STATES = frozenset(
    {
        "offline",
        "suspect",
        "recovering",
        "in_recovery",
        "restoring",
        "recovery_pending",
        "standby",
        "in_standby",
        "emergency",
        "copying",
    }
)

#: ``sys.databases.state`` numeric code -> normalised state name.
STATE_CODE_NAMES: dict[int, str] = {
    0: "online",
    1: "restoring",
    2: "recovering",
    3: "recovery_pending",
    4: "suspect",
    5: "emergency",
    6: "offline",
    7: "copying",
}


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


def _truthy(raw: Any) -> bool:
    """Return whether ``raw`` is a true-ish SQL bit/boolean value."""
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return raw != 0
    if raw is None:
        return False
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def normalize_state(raw: Any) -> str | None:
    """Normalise a ``state_desc``/``state`` value to a lowercase state name.

    ``"RECOVERY_PENDING"`` and ``"IN RECOVERY"`` become ``recovery_pending`` and
    ``in_recovery``. A numeric ``state`` code is mapped through
    :data:`STATE_CODE_NAMES`. Missing or blank values return ``None``.
    """
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return STATE_CODE_NAMES.get(raw, str(raw))
    text = str(raw).strip()
    if not text:
        return None
    return text.lower().replace(" ", "_").replace("-", "_")


def _state_name(row: Any) -> str | None:
    """The effective state name for ``row``.

    ``state_desc`` wins; the numeric ``state`` column is the fallback. A
    database with ``is_in_standby`` set is reported as ``standby`` even when its
    ``state_desc`` is ``ONLINE`` (the source query explicitly surfaces it).
    """
    name = normalize_state(value(row, "state_desc", None))
    if name is None:
        name = normalize_state(_as_int(value(row, "state", None)))
    if _truthy(value(row, "is_in_standby", None)) and name not in NON_NORMAL_STATES:
        return "standby"
    return name


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


def _finding(row: Any, state: str) -> EvidenceRow:
    """Build one :class:`EvidenceRow` for a database in a non-normal state."""
    name = _database_name(row)
    return EvidenceRow(
        area=AREA,
        timestamp=None,
        description=f"Database '{name}' is not available (state: {state}).",
        status=SourceStatus.collected,
        details={
            "database_name": name,
            "state": state,
            "state_desc": value(row, "state_desc", None),
            "state_code": _as_int(value(row, "state", None)),
            "user_access": value(row, "user_access_desc", None),
            "is_in_standby": value(row, "is_in_standby", None),
            "recovery_model": value(row, "recovery_model_desc", None),
        },
    )


class AvailabilityCollector:
    """One row per in-scope database in a non-normal state.

    ``databases`` is the #9 scope: ``None`` for the whole instance, or an
    explicit list of database names. Accessible databases and out-of-scope
    databases both produce no rows while the source stays ``collected``.
    """

    source_id = SOURCE_ID

    def __init__(self, databases: Sequence[str] | None = None) -> None:
        self._databases = list(databases) if databases is not None else None

    def collect(
        self, evidence: SourceEvidence, registry: EvidenceRegistry
    ) -> list[EvidenceRow]:
        if evidence.status is not QueryStatus.ok:
            status, note = failure_status(evidence)
            registry.record(self.source_id, status, note)
            return []

        findings: list[EvidenceRow] = []
        for row in evidence.rows:
            name = _database_name(row)
            if not _in_scope(name, self._databases):
                continue
            state = _state_name(row)
            if state is None or state not in NON_NORMAL_STATES:
                continue
            findings.append(_finding(row, state))

        registry.record(
            self.source_id,
            SourceStatus.collected,
            f"{len(findings)} database(s) in a non-normal state.",
        )
        return findings


def collect_availability(
    evidence: SourceEvidence,
    registry: EvidenceRegistry,
    *,
    databases: Sequence[str] | None = None,
) -> list[EvidenceRow]:
    """Run the availability collector and return its structured rows."""
    return AvailabilityCollector(databases).collect(evidence, registry)
