"""Evidence models and the source-inventory registry (#12).

Import surface for the run payload schema (:mod:`app.evidence.models`) and the
registry (:mod:`app.evidence.registry`). Collectors (#13–#16), the execution
engine, and the reporting/persistence layers (#19, #21) all share these types.
"""

from app.evidence.collectors import (
    Collector,
    SourceEvidence,
    failure_status,
    value,
)
from app.evidence.models import (
    DiagnosticRunResult,
    EvidenceRow,
    EvidenceSource,
    QueryEvidence,
    QueryStatus,
    SourceStatus,
)
from app.evidence.registry import (
    APPLICABILITY_PREDICATES,
    EvidenceRegistry,
    SourceApplicability,
    is_applicable,
)

__all__ = [
    "APPLICABILITY_PREDICATES",
    "Collector",
    "DiagnosticRunResult",
    "EvidenceRegistry",
    "EvidenceRow",
    "EvidenceSource",
    "QueryEvidence",
    "QueryStatus",
    "SourceApplicability",
    "SourceEvidence",
    "SourceStatus",
    "failure_status",
    "is_applicable",
    "value",
]
