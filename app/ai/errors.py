"""Categorized errors for the #18 AI correlation layer.

An :class:`LLMError` never carries a raw provider exception or any credential
material: it exposes a stable :class:`LLMErrorCategory` and a short, safe
message built only from that category and a fixed phrase. Callers (the arq job)
can therefore mark a run ``failed`` without leaking a vendor stack trace into a
stored result or a log line.
"""

from __future__ import annotations

from enum import Enum


class LLMErrorCategory(str, Enum):
    """The categorized reasons a correlation may fail outright."""

    malformed_json = "malformed-json"
    schema_invalid = "schema-invalid"
    out_of_range = "out-of-range"
    provider_error = "provider-error"


class LLMError(RuntimeError):
    """A categorized, credential-free failure of the correlation call.

    ``category`` is one of :class:`LLMErrorCategory`; ``message`` is a short
    phrase that never includes provider output, an API key, or a raw exception.
    """

    def __init__(self, category: LLMErrorCategory, message: str) -> None:
        self.category = category
        super().__init__(f"LLM correlation failed ({category.value}): {message}")


class LLMTimeoutError(Exception):
    """The provider did not answer within the configured timeout.

    Distinct from :class:`LLMError`: a timeout is handled as an *inconclusive*
    result, not a failed run (plan.md §5, #18).
    """
