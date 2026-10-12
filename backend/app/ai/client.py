"""Provider-agnostic correlation entry point (#18).

:func:`correlate` turns a :class:`~app.ai.schemas.CorrelationRequest` (built only
from #17 signals and #12 evidence summaries) into a schema-validated
:class:`~app.ai.schemas.Verdict`.

Two modes:

* **No provider** -> the built-in, deterministic heuristics in
  :mod:`app.ai.correlation` run; there is no network call at all.
* **Provider injected** -> the provider's text is parsed and validated. Malformed
  output (non-JSON, missing fields, out-of-range confidence) is retried once with
  a corrected instruction; if it is still bad the call fails with a categorized
  :class:`~app.ai.errors.LLMError`. A provider timeout is treated as an
  *inconclusive* result, not a failed run.

The prompt is assembled from signals and evidence summaries only, and every
interpolated string passes through the #5 redaction helper. No credential ever
reaches the prompt, a log line, or an exception message.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.ai.correlation import apply_inconclusive_rule, heuristic_verdict
from app.ai.errors import LLMError, LLMErrorCategory, LLMTimeoutError
from app.ai.providers import LLMProvider
from app.ai.schemas import (
    INSUFFICIENT_EVIDENCE_DESCRIPTION,
    MAX_ALTERNATIVES,
    Cause,
    ConfidenceLabel,
    CorrelationRequest,
    Verdict,
    confidence_label,
)
from app.config import DEFAULT_LLM_TIMEOUT_SECONDS, Settings
from app.logredact import redact_text
from app.rules.signals import Severity

logger = logging.getLogger(__name__)

#: Sentinel returned when a provider call timed out.
_TIMEOUT = object()

#: The JSON schema the prompt asks the provider to honour.
_SCHEMA_HINT = (
    '{"primary_cause": {"area": "<issue area>", "description": "<sentence>"}, '
    '"alternatives": [{"area": "<issue area>", "description": "<sentence>"}], '
    '"confidence_percent": <integer 0-100>, '
    '"severity": "<low|medium|high>", "rationale": "<sentence>"}'
)

_VALID_SEVERITIES = {severity.value for severity in Severity}


class _MalformedOutput(Exception):
    """Internal signal that the provider output failed validation."""

    def __init__(self, category: LLMErrorCategory, detail: str) -> None:
        self.category = category
        self.detail = detail
        super().__init__(detail)


def _correction_text(category: LLMErrorCategory) -> str:
    """The corrected instruction appended to the retry prompt."""
    if category is LLMErrorCategory.malformed_json:
        return (
            "Your previous reply was not valid JSON. Reply with one JSON object "
            "and nothing else."
        )
    if category is LLMErrorCategory.out_of_range:
        return (
            "Your previous reply had confidence_percent outside 0-100. Use an "
            "integer from 0 to 100."
        )
    return (
        "Your previous reply was missing required fields or used invalid values. "
        f"Match exactly: {_SCHEMA_HINT}. Alternatives are limited to "
        f"{MAX_ALTERNATIVES}."
    )


def build_prompt(
    request: CorrelationRequest, *, correction: LLMErrorCategory | None = None
) -> str:
    """Assemble the provider prompt from signals and evidence summaries only."""
    signals = [
        {
            "area": signal.area,
            "severity": signal.severity.value,
            "description": redact_text(signal.description),
            "evidence_ids": list(signal.evidence_ids),
        }
        for signal in request.signals
    ]
    summaries = [
        {
            "source_id": summary.source_id,
            "issue_area": summary.issue_area,
            "status": summary.status.value,
            "title": redact_text(summary.title),
            "note": redact_text(summary.note),
        }
        for summary in request.evidence_summaries
    ]
    lines = [
        "Correlate the following SQL Server diagnostic data into a probable cause.",
        f"Return only a JSON object matching: {_SCHEMA_HINT}.",
        "RULE SIGNALS:",
        json.dumps(signals, sort_keys=True, default=str),
        "EVIDENCE SUMMARIES:",
        json.dumps(summaries, sort_keys=True, default=str),
    ]
    if correction is not None:
        lines.append(f"Correction: {_correction_text(correction)}")
    return "\n".join(lines)


def _strip_code_fence(text: str) -> str:
    """Remove a leading/trailing ``` fence if the provider wrapped its JSON."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _cause_from(entry: Any) -> tuple[str, str, int, ConfidenceLabel]:
    """Validate one cause mapping into (area, description, percent, label)."""
    if not isinstance(entry, dict):
        raise _MalformedOutput(
            LLMErrorCategory.schema_invalid, "a cause was not a JSON object"
        )
    area = entry.get("area")
    description = entry.get("description")
    if not isinstance(area, str) or not area.strip():
        raise _MalformedOutput(
            LLMErrorCategory.schema_invalid, "a cause had no area"
        )
    if not isinstance(description, str) or not description.strip():
        raise _MalformedOutput(
            LLMErrorCategory.schema_invalid, "a cause had no description"
        )
    percent = entry.get("confidence_percent", 0)
    if isinstance(percent, bool) or not isinstance(percent, (int, float)):
        raise _MalformedOutput(
            LLMErrorCategory.schema_invalid, "a cause confidence was not a number"
        )
    percent = int(percent)
    if percent < 0 or percent > 100:
        raise _MalformedOutput(
            LLMErrorCategory.out_of_range, "a cause confidence was outside 0-100"
        )
    return area.strip(), description.strip(), percent, confidence_label(percent)


def _verdict_from_mapping(data: dict[str, Any]) -> Verdict:
    """Validate a parsed JSON object into a :class:`Verdict`."""
    missing = [
        key
        for key in ("primary_cause", "confidence_percent", "severity", "rationale")
        if key not in data
    ]
    if missing:
        raise _MalformedOutput(
            LLMErrorCategory.schema_invalid,
            f"missing required field(s): {', '.join(missing)}",
        )

    raw_percent = data["confidence_percent"]
    if isinstance(raw_percent, bool) or not isinstance(raw_percent, (int, float)):
        raise _MalformedOutput(
            LLMErrorCategory.schema_invalid, "confidence_percent was not a number"
        )
    percent = int(raw_percent)
    if percent < 0 or percent > 100:
        raise _MalformedOutput(
            LLMErrorCategory.out_of_range, "confidence_percent was outside 0-100"
        )

    severity_raw = data["severity"]
    if not isinstance(severity_raw, str) or severity_raw.strip().lower() not in _VALID_SEVERITIES:
        raise _MalformedOutput(
            LLMErrorCategory.schema_invalid, "severity was not low, medium, or high"
        )

    rationale = data["rationale"]
    if not isinstance(rationale, str) or not rationale.strip():
        raise _MalformedOutput(
            LLMErrorCategory.schema_invalid, "rationale was empty"
        )

    area, description, _, _ = _cause_from(data["primary_cause"])

    alternatives_raw = data.get("alternatives", [])
    if not isinstance(alternatives_raw, list):
        raise _MalformedOutput(
            LLMErrorCategory.schema_invalid, "alternatives was not a list"
        )
    if len(alternatives_raw) > MAX_ALTERNATIVES:
        raise _MalformedOutput(
            LLMErrorCategory.schema_invalid,
            f"more than {MAX_ALTERNATIVES} alternatives were returned",
        )
    alternatives = [
        Cause(
            area=alt_area,
            description=alt_description,
            confidence_percent=alt_percent,
            confidence_label=alt_label,
        )
        for alt_area, alt_description, alt_percent, alt_label in (
            _cause_from(entry) for entry in alternatives_raw
        )
    ]

    return Verdict(
        primary_cause=Cause(
            area=area,
            description=description,
            confidence_percent=percent,
            confidence_label=confidence_label(percent),
        ),
        alternatives=alternatives,
        confidence_percent=percent,
        confidence_label=confidence_label(percent),
        severity=Severity(severity_raw.strip().lower()),
        rationale=rationale.strip(),
    )


def parse_verdict(raw: str) -> Verdict:
    """Parse and validate a provider's raw text into a :class:`Verdict`.

    Raises the internal ``_MalformedOutput`` for anything invalid; :func:`correlate`
    turns that into a retry and then a categorized :class:`LLMError`.
    """
    if not isinstance(raw, str):
        raise _MalformedOutput(
            LLMErrorCategory.schema_invalid, "the provider response was not text"
        )
    text = _strip_code_fence(raw)
    if not text:
        raise _MalformedOutput(
            LLMErrorCategory.malformed_json, "the provider response was empty"
        )
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        raise _MalformedOutput(
            LLMErrorCategory.malformed_json, "the provider response was not valid JSON"
        ) from None
    if not isinstance(data, dict):
        raise _MalformedOutput(
            LLMErrorCategory.schema_invalid, "the provider response was not a JSON object"
        )
    return _verdict_from_mapping(data)


def _complete(
    provider: LLMProvider, prompt: str, timeout: float | None
) -> str | object:
    """Call the provider, mapping a timeout to the sentinel and other errors to LLMError."""
    try:
        return provider.complete(prompt, timeout=timeout)
    except (LLMTimeoutError, TimeoutError):
        return _TIMEOUT
    except LLMError:
        raise
    except Exception:  # noqa: BLE001 - categorize, never leak the raw exception
        raise LLMError(
            LLMErrorCategory.provider_error,
            "the LLM provider raised an unexpected error",
        ) from None


def _inconclusive_verdict(request: CorrelationRequest, reason: str) -> Verdict:
    """Build a forced-inconclusive verdict from the built-in heuristics."""
    base = heuristic_verdict(request)
    forced = base.model_copy(
        update={"confidence_percent": 0, "confidence_label": ConfidenceLabel.low}
    )
    verdict = apply_inconclusive_rule(forced, request)
    return verdict.model_copy(
        update={
            "inconclusive": True,
            "rationale": (
                f"{INSUFFICIENT_EVIDENCE_DESCRIPTION.capitalize()}: correlation "
                f"was {reason}."
            ),
        }
    )


def _effective_timeout(timeout: float | None, settings: Settings | None) -> float:
    """Resolve the provider timeout: explicit, then settings, then the default."""
    if timeout is not None:
        return timeout
    if settings is not None:
        return settings.llm_timeout_seconds
    return DEFAULT_LLM_TIMEOUT_SECONDS


def correlate(
    request_payload: CorrelationRequest,
    *,
    provider: LLMProvider | None = None,
    timeout: float | None = None,
    settings: Settings | None = None,
) -> Verdict:
    """Correlate rule signals and evidence summaries into a verdict.

    With no ``provider`` the built-in heuristics run and no network is touched.
    With a provider, the response is parsed and validated with one retry; a
    timeout yields an inconclusive verdict and persistent malformation raises a
    categorized :class:`~app.ai.errors.LLMError`.
    """
    if provider is None:
        verdict = heuristic_verdict(request_payload)
        logger.info(
            "correlation (heuristics) inconclusive=%s confidence=%d alternatives=%d",
            verdict.inconclusive,
            verdict.confidence_percent,
            len(verdict.alternatives),
        )
        return verdict

    effective_timeout = _effective_timeout(timeout, settings)
    raw = _complete(provider, build_prompt(request_payload), effective_timeout)
    if raw is _TIMEOUT:
        return _inconclusive_verdict(request_payload, "interrupted by a provider timeout")

    try:
        verdict = parse_verdict(raw)
    except _MalformedOutput as first:
        retry_raw = _complete(
            provider,
            build_prompt(request_payload, correction=first.category),
            effective_timeout,
        )
        if retry_raw is _TIMEOUT:
            return _inconclusive_verdict(
                request_payload, "interrupted by a provider timeout"
            )
        try:
            verdict = parse_verdict(retry_raw)
        except _MalformedOutput as second:
            raise LLMError(second.category, second.detail) from None

    verdict = apply_inconclusive_rule(verdict, request_payload)
    logger.info(
        "correlation verdict inconclusive=%s confidence=%d alternatives=%d",
        verdict.inconclusive,
        verdict.confidence_percent,
        len(verdict.alternatives),
    )
    return verdict
