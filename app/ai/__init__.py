"""AI correlation layer (#18).

Provider-agnostic correlation of #17 rule signals plus #12 evidence summaries
into a schema-validated :class:`~app.ai.schemas.Verdict`, with built-in,
deterministic heuristics as the no-network default. The shared
``INCONCLUSIVE_THRESHOLD`` and the :class:`Verdict` shape are defined once in
:mod:`app.ai.schemas` for #19/#26 to consume.
"""

from app.ai.client import build_prompt, correlate, parse_verdict
from app.ai.correlation import (
    apply_inconclusive_rule,
    evidence_quality_insufficient,
    heuristic_verdict,
)
from app.ai.errors import LLMError, LLMErrorCategory, LLMTimeoutError
from app.ai.providers import (
    DEFAULT_LLM_MODEL,
    SYSTEM_INSTRUCTION,
    HttpLLMProvider,
    LLMProvider,
)
from app.ai.schemas import (
    CONFIDENCE_LOW_MAX,
    CONFIDENCE_MEDIUM_MAX,
    INCONCLUSIVE_THRESHOLD,
    INSUFFICIENT_EVIDENCE_DESCRIPTION,
    MAX_ALTERNATIVES,
    Cause,
    ConfidenceLabel,
    CorrelationRequest,
    EvidenceSummary,
    Verdict,
    confidence_label,
    is_inconclusive,
)

__all__ = [
    "CONFIDENCE_LOW_MAX",
    "CONFIDENCE_MEDIUM_MAX",
    "DEFAULT_LLM_MODEL",
    "INCONCLUSIVE_THRESHOLD",
    "INSUFFICIENT_EVIDENCE_DESCRIPTION",
    "MAX_ALTERNATIVES",
    "SYSTEM_INSTRUCTION",
    "Cause",
    "ConfidenceLabel",
    "CorrelationRequest",
    "EvidenceSummary",
    "HttpLLMProvider",
    "LLMError",
    "LLMErrorCategory",
    "LLMProvider",
    "LLMTimeoutError",
    "Verdict",
    "apply_inconclusive_rule",
    "build_prompt",
    "confidence_label",
    "correlate",
    "evidence_quality_insufficient",
    "heuristic_verdict",
    "is_inconclusive",
    "parse_verdict",
]
