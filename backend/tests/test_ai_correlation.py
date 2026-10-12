"""Unit tests for the #18 AI correlation + inconclusive handling.

Everything runs with no network and no SQL Server. Two provider paths are
covered:

* the built-in deterministic heuristics (the no-provider default), and
* an injected mocked provider, including malformed-output retry, timeouts, and
  the confidence-label / inconclusive boundaries.

:class:`HttpLLMProvider` is exercised through an ``httpx.MockTransport`` so its
request shape, timeout mapping, and secret-free errors are pinned without a live
endpoint.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.ai import (
    CONFIDENCE_LOW_MAX,
    CONFIDENCE_MEDIUM_MAX,
    INCONCLUSIVE_THRESHOLD,
    INSUFFICIENT_EVIDENCE_DESCRIPTION,
    MAX_ALTERNATIVES,
    Cause,
    ConfidenceLabel,
    CorrelationRequest,
    EvidenceSummary,
    HttpLLMProvider,
    LLMError,
    LLMErrorCategory,
    LLMTimeoutError,
    Verdict,
    build_prompt,
    confidence_label,
    correlate,
    evidence_quality_insufficient,
    heuristic_verdict,
    is_inconclusive,
    parse_verdict,
)
from app.config import Settings
from app.evidence.models import EvidenceSource, SourceStatus
from app.rules.signals import Severity, Signal

SECRET = "hunter2-s3cret"
COLLECTED = SourceStatus.collected
UNAVAILABLE = SourceStatus.unavailable_permission


# --- fixtures / helpers -----------------------------------------------------


def _signal(area: str, severity: Severity, description: str = "", evidence="e1") -> Signal:
    return Signal(
        area=area,
        severity=severity,
        description=description or f"{area} signal",
        evidence_ids=[evidence],
    )


def _source(
    source_id: str,
    area: str,
    status: SourceStatus = COLLECTED,
    *,
    note: str = "",
) -> EvidenceSource:
    return EvidenceSource(
        query_id=source_id,
        title=f"{area} source",
        category=area,
        issue_area=area,
        status=status,
        note=note,
    )


def _request(
    signals: list[Signal] | None = None,
    sources: list[EvidenceSource] | None = None,
) -> CorrelationRequest:
    if signals is None:
        signals = [_signal("blocking", Severity.high)]
    if sources is None:
        sources = [_source("q", "blocking", COLLECTED)]
    return CorrelationRequest.build(signals, sources)


def _response(
    percent: int = 82,
    *,
    severity: str = "high",
    alternatives: list[dict] | None = None,
) -> str:
    return json.dumps(
        {
            "primary_cause": {"area": "blocking", "description": "blocking on session 57"},
            "alternatives": alternatives
            if alternatives is not None
            else [{"area": "storage", "description": "low free space"}],
            "confidence_percent": percent,
            "severity": severity,
            "rationale": "blocking dominates the window",
        }
    )


class ScriptedProvider:
    """A provider that returns queued strings or raises queued exceptions."""

    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, float | None]] = []

    def complete(self, prompt: str, *, timeout: float | None = None) -> str:
        self.calls.append((prompt, timeout))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class BoomProvider:
    """A provider that raises an unexpected, secret-bearing exception."""

    def complete(self, prompt: str, *, timeout: float | None = None) -> str:
        raise RuntimeError(f"upstream exploded with {SECRET}")


# --- confidence label boundaries --------------------------------------------


@pytest.mark.parametrize(
    ("percent", "expected"),
    [
        (0, ConfidenceLabel.low),
        (49, ConfidenceLabel.low),
        (50, ConfidenceLabel.medium),
        (79, ConfidenceLabel.medium),
        (80, ConfidenceLabel.high),
        (100, ConfidenceLabel.high),
    ],
)
def test_confidence_label_bands(percent, expected):
    assert confidence_label(percent) is expected


def test_confidence_band_constants_match_the_documented_boundaries():
    assert (CONFIDENCE_LOW_MAX, CONFIDENCE_MEDIUM_MAX) == (49, 79)
    assert INCONCLUSIVE_THRESHOLD == 0.5


def test_is_inconclusive_uses_the_shared_threshold_and_evidence_quality():
    assert is_inconclusive(50, evidence_quality_insufficient=False) is False
    assert is_inconclusive(49, evidence_quality_insufficient=False) is True
    assert is_inconclusive(100, evidence_quality_insufficient=True) is True


def test_verdict_rejects_a_label_that_does_not_match_its_percentage():
    with pytest.raises(ValueError):
        Verdict(
            primary_cause=Cause(area="blocking", description="x"),
            confidence_percent=80,
            confidence_label=ConfidenceLabel.low,
            severity=Severity.high,
            rationale="x",
        )


# --- built-in heuristics (no provider, no network) --------------------------


def test_heuristic_correlates_overlapping_signals_into_a_primary_cause():
    request = _request(
        signals=[
            _signal("blocking", Severity.high, "high blocking", "e1"),
            _signal("blocking", Severity.medium, "medium blocking", "e2"),
            _signal("storage", Severity.low, "slight pressure", "e3"),
        ],
        sources=[_source("q1", "blocking"), _source("q2", "storage")],
    )

    verdict = heuristic_verdict(request)

    # blocking scores 3 + 2 = 5, storage 1, so blocking is primary.
    assert verdict.primary_cause.area == "blocking"
    assert verdict.primary_cause.description == "high blocking"
    assert [alt.area for alt in verdict.alternatives] == ["storage"]
    assert verdict.severity is Severity.high
    assert verdict.inconclusive is False
    assert verdict.confidence_label is confidence_label(verdict.confidence_percent)


def test_heuristic_ties_break_on_the_fixed_area_order():
    request = _request(
        signals=[
            _signal("storage", Severity.high, "storage high", "s"),
            _signal("deadlocks", Severity.high, "deadlock high", "d"),
        ],
        sources=[_source("q1", "storage"), _source("q2", "deadlocks")],
    )

    verdict = heuristic_verdict(request)

    # Both score 3; deadlocks precedes storage in AREA_ORDER.
    assert verdict.primary_cause.area == "deadlocks"
    assert [alt.area for alt in verdict.alternatives] == ["storage"]


def test_heuristic_caps_alternatives_at_the_shared_maximum():
    areas = ["blocking", "deadlocks", "queries", "errors", "storage"]
    request = _request(
        signals=[_signal(area, Severity.high, f"{area} high", area) for area in areas],
        sources=[_source(f"q{index}", area) for index, area in enumerate(areas)],
    )

    verdict = heuristic_verdict(request)

    assert len(verdict.alternatives) == MAX_ALTERNATIVES


def test_heuristic_result_is_deterministic_and_idempotent():
    request = _request(
        signals=[
            _signal("blocking", Severity.high, "a", "e1"),
            _signal("errors", Severity.medium, "b", "e2"),
        ],
        sources=[_source("q1", "blocking"), _source("q2", "errors")],
    )

    first = heuristic_verdict(request).model_dump()
    second = heuristic_verdict(request).model_dump()
    third = heuristic_verdict(request.model_copy(deep=True)).model_dump()

    assert first == second == third


def test_empty_signals_yield_an_inconclusive_zero_confidence_verdict():
    verdict = heuristic_verdict(_request(signals=[], sources=[_source("q", "blocking")]))

    assert verdict.inconclusive is True
    assert verdict.confidence_percent == 0
    assert verdict.confidence_label is ConfidenceLabel.low
    assert verdict.primary_cause.description == INSUFFICIENT_EVIDENCE_DESCRIPTION
    assert verdict.alternatives == []


def test_evidence_quality_is_insufficient_when_every_source_is_unavailable():
    summaries = [
        EvidenceSummary(
            source_id="q1", issue_area="blocking", status=UNAVAILABLE, note="denied"
        ),
        EvidenceSummary(
            source_id="q2", issue_area="storage", status=UNAVAILABLE, note="denied"
        ),
    ]
    assert evidence_quality_insufficient(summaries) is True
    assert evidence_quality_insufficient([]) is True
    assert evidence_quality_insufficient(
        [EvidenceSummary(source_id="q", issue_area="blocking", status=COLLECTED)]
    ) is False


def test_insufficient_evidence_forces_inconclusive_with_alternatives_intact():
    request = _request(
        signals=[
            _signal("blocking", Severity.high, "blocking high", "e1"),
            _signal("storage", Severity.high, "storage high", "e2"),
        ],
        sources=[
            _source("q1", "blocking", UNAVAILABLE),
            _source("q2", "storage", UNAVAILABLE),
        ],
    )

    verdict = heuristic_verdict(request)

    assert verdict.inconclusive is True
    assert verdict.confidence_percent <= CONFIDENCE_LOW_MAX
    assert verdict.confidence_label is ConfidenceLabel.low
    assert verdict.primary_cause.description == INSUFFICIENT_EVIDENCE_DESCRIPTION
    assert [alt.area for alt in verdict.alternatives] == ["storage"]
    assert all(alt.confidence_label is ConfidenceLabel.low for alt in verdict.alternatives)


def test_partial_evidence_is_treated_as_sufficient():
    request = _request(
        signals=[_signal("blocking", Severity.high, "blocking high", "e1")],
        sources=[
            _source("q1", "blocking", COLLECTED),
            _source("q2", "storage", UNAVAILABLE),
        ],
    )

    assert heuristic_verdict(request).inconclusive is False


def test_default_correlation_never_touches_the_network(monkeypatch):
    def _boom(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("the default correlation path must not use httpx")

    monkeypatch.setattr("app.ai.providers.httpx.Client", _boom)

    verdict = correlate(_request())

    assert isinstance(verdict, Verdict)


# --- mocked provider: valid / malformed / retry -----------------------------


def test_valid_provider_response_is_parsed():
    provider = ScriptedProvider([_response(percent=85)])
    request = _request()

    verdict = correlate(request, provider=provider, timeout=60)

    assert verdict.confidence_percent == 85
    assert verdict.confidence_label is ConfidenceLabel.high
    assert verdict.primary_cause.area == "blocking"
    assert [alt.area for alt in verdict.alternatives] == ["storage"]
    assert verdict.severity is Severity.high
    assert verdict.inconclusive is False
    assert len(provider.calls) == 1
    assert provider.calls[0][1] == 60


def test_non_json_response_is_retried_once_then_raises_categorized_error():
    provider = ScriptedProvider(["not json", "still not json"])

    with pytest.raises(LLMError) as excinfo:
        correlate(_request(), provider=provider)

    assert excinfo.value.category is LLMErrorCategory.malformed_json
    assert len(provider.calls) == 2
    assert "Correction:" in provider.calls[1][0]
    assert "not json" not in str(excinfo.value)


def test_invalid_json_then_valid_succeeds_on_the_retry():
    provider = ScriptedProvider(["nope", _response(percent=60)])

    verdict = correlate(_request(), provider=provider)

    assert verdict.confidence_percent == 60
    assert len(provider.calls) == 2
    assert "Correction:" not in provider.calls[0][0]
    assert "Correction:" in provider.calls[1][0]


def test_missing_fields_are_retried_then_raise_schema_error():
    provider = ScriptedProvider(['{"confidence_percent": 80}', '{"confidence_percent": 80}'])

    with pytest.raises(LLMError) as excinfo:
        correlate(_request(), provider=provider)

    assert excinfo.value.category is LLMErrorCategory.schema_invalid
    assert len(provider.calls) == 2


def test_out_of_range_confidence_is_retried_then_raises_range_error():
    provider = ScriptedProvider([_response(percent=150), _response(percent=-1)])

    with pytest.raises(LLMError) as excinfo:
        correlate(_request(), provider=provider)

    assert excinfo.value.category is LLMErrorCategory.out_of_range
    assert len(provider.calls) == 2


def test_out_of_range_on_retry_then_valid_succeeds():
    provider = ScriptedProvider([_response(percent=150), _response(percent=75)])

    verdict = correlate(_request(), provider=provider)

    assert verdict.confidence_percent == 75
    assert verdict.confidence_label is ConfidenceLabel.medium


def test_too_many_alternatives_is_schema_invalid():
    many = [
        {"area": f"area-{index}", "description": f"cause {index}"}
        for index in range(MAX_ALTERNATIVES + 1)
    ]
    provider = ScriptedProvider([_response(alternatives=many), _response(alternatives=many)])

    with pytest.raises(LLMError) as excinfo:
        correlate(_request(), provider=provider)

    assert excinfo.value.category is LLMErrorCategory.schema_invalid


def test_code_fenced_json_is_accepted():
    provider = ScriptedProvider([f"```json\n{_response(percent=81)}\n```"])

    verdict = correlate(_request(), provider=provider)

    assert verdict.confidence_percent == 81


# --- mocked provider: timeouts ----------------------------------------------


def test_timeout_is_inconclusive_and_keeps_alternatives():
    request = _request(
        signals=[
            _signal("blocking", Severity.high, "blocking high", "e1"),
            _signal("storage", Severity.medium, "storage medium", "e2"),
        ],
        sources=[_source("q1", "blocking"), _source("q2", "storage")],
    )
    provider = ScriptedProvider([LLMTimeoutError("timed out")])

    verdict = correlate(request, provider=provider)

    assert verdict.inconclusive is True
    assert verdict.confidence_label is ConfidenceLabel.low
    assert verdict.primary_cause.description == INSUFFICIENT_EVIDENCE_DESCRIPTION
    assert [alt.area for alt in verdict.alternatives] == ["storage"]
    assert len(provider.calls) == 1


def test_timeout_on_the_retry_is_also_inconclusive():
    provider = ScriptedProvider(["not json", LLMTimeoutError("timed out")])

    verdict = correlate(_request(), provider=provider)

    assert verdict.inconclusive is True
    assert len(provider.calls) == 2


# --- mocked provider: confidence boundaries + inconclusive ------------------


@pytest.mark.parametrize(
    ("percent", "expected"),
    [
        (49, ConfidenceLabel.low),
        (50, ConfidenceLabel.medium),
        (79, ConfidenceLabel.medium),
        (80, ConfidenceLabel.high),
        (100, ConfidenceLabel.high),
    ],
)
def test_provider_percentage_produces_the_expected_label(percent, expected):
    verdict = correlate(_request(), provider=ScriptedProvider([_response(percent=percent)]))

    assert verdict.confidence_percent == percent
    assert verdict.confidence_label is expected
    assert verdict.inconclusive is (percent < 50)


def test_below_fifty_is_inconclusive_with_alternatives_intact():
    provider = ScriptedProvider([_response(percent=30)])

    verdict = correlate(_request(), provider=provider)

    assert verdict.inconclusive is True
    assert verdict.confidence_percent <= CONFIDENCE_LOW_MAX
    assert verdict.confidence_label is ConfidenceLabel.low
    assert verdict.primary_cause.description == INSUFFICIENT_EVIDENCE_DESCRIPTION
    assert [alt.area for alt in verdict.alternatives] == ["storage"]
    assert all(alt.confidence_label is ConfidenceLabel.low for alt in verdict.alternatives)


def test_high_confidence_with_unavailable_evidence_is_still_inconclusive():
    request = _request(
        signals=[_signal("blocking", Severity.high, "blocking high", "e1")],
        sources=[_source("q1", "blocking", UNAVAILABLE)],
    )
    provider = ScriptedProvider([_response(percent=95)])

    verdict = correlate(request, provider=provider)

    assert verdict.inconclusive is True
    assert verdict.confidence_percent <= CONFIDENCE_LOW_MAX
    assert verdict.primary_cause.description == INSUFFICIENT_EVIDENCE_DESCRIPTION


# --- mocked provider: errors never leak secrets -----------------------------


def test_categorized_provider_error_propagates():
    provider = ScriptedProvider(
        [LLMError(LLMErrorCategory.provider_error, "the LLM provider returned status 500")]
    )

    with pytest.raises(LLMError) as excinfo:
        correlate(_request(), provider=provider)

    assert excinfo.value.category is LLMErrorCategory.provider_error


def test_unexpected_provider_exception_is_wrapped_without_the_raw_message():
    with pytest.raises(LLMError) as excinfo:
        correlate(_request(), provider=BoomProvider())

    assert excinfo.value.category is LLMErrorCategory.provider_error
    assert SECRET not in str(excinfo.value)
    assert "upstream exploded" not in str(excinfo.value)


# --- prompt assembly / redaction --------------------------------------------


def test_prompt_is_built_only_from_signals_and_evidence_summaries():
    request = _request()
    prompt = build_prompt(request)

    assert "RULE SIGNALS:" in prompt
    assert "EVIDENCE SUMMARIES:" in prompt
    assert "blocking signal" in prompt
    assert "blocking source" in prompt


def test_prompt_redacts_secret_shaped_values_in_notes_and_descriptions():
    request = _request(
        signals=[_signal("blocking", Severity.high, f"login failed password={SECRET}", "e1")],
        sources=[_source("q", "blocking", COLLECTED, note=f"pwd={SECRET}")],
    )

    prompt = build_prompt(request)

    assert SECRET not in prompt
    assert "****" in prompt


def test_prompt_appends_a_correction_only_when_asked():
    request = _request()

    assert "Correction:" not in build_prompt(request)
    assert "Correction:" in build_prompt(
        request, correction=LLMErrorCategory.out_of_range
    )


# --- HTTP provider (MockTransport, no network) ------------------------------


def _http_provider(handler) -> HttpLLMProvider:
    return HttpLLMProvider(
        endpoint="https://llm.example.com/v1",
        api_key="api-key-value",
        model="test-model",
        transport=httpx.MockTransport(handler),
    )


def test_http_provider_sends_an_openai_compatible_request_and_returns_content():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": _response(percent=80)}}]}
        )

    provider = _http_provider(handler)
    content = provider.complete("hello")

    assert content == _response(percent=80)
    assert seen["url"] == "https://llm.example.com/v1/chat/completions"
    assert seen["auth"] == "Bearer api-key-value"
    assert seen["body"]["model"] == "test-model"
    assert seen["body"]["messages"][-1]["content"] == "hello"


def test_http_provider_maps_timeout_to_llm_timeout_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("too slow")

    provider = _http_provider(handler)

    with pytest.raises(LLMTimeoutError):
        provider.complete("hello")


def test_http_provider_maps_error_status_to_a_secret_free_llm_error():
    provider = _http_provider(lambda request: httpx.Response(500, json={"error": "boom"}))

    with pytest.raises(LLMError) as excinfo:
        provider.complete("hello")

    assert excinfo.value.category is LLMErrorCategory.provider_error
    assert "api-key-value" not in str(excinfo.value)


def test_http_provider_maps_a_bad_envelope_to_malformed_json():
    provider = _http_provider(lambda request: httpx.Response(200, json={"nope": True}))

    with pytest.raises(LLMError) as excinfo:
        provider.complete("hello")

    assert excinfo.value.category is LLMErrorCategory.malformed_json


def test_http_provider_from_settings_uses_the_configured_endpoint():
    settings = Settings(
        database_url="mssql://x",
        redis_url="redis://x/0",
        oidc_issuer="https://issuer.example.com",
        oidc_client_id="c",
        oidc_client_secret="s",
        llm_endpoint="https://llm.example.com/v1",
        llm_api_key="api-key-value",
        llm_timeout_seconds=15,
    )

    provider = HttpLLMProvider.from_settings(settings)

    assert provider._endpoint == "https://llm.example.com/v1"
    assert provider._timeout == 15


# --- parse_verdict direct contract ------------------------------------------


def test_parse_verdict_rejects_out_of_range_percentage():
    payload = json.loads(_response(percent=80))
    payload["confidence_percent"] = 101
    with pytest.raises(LLMError):
        correlate(
            _request(),
            provider=ScriptedProvider([json.dumps(payload), json.dumps(payload)]),
        )


def test_parse_verdict_accepts_a_minimal_valid_object():
    verdict = parse_verdict(_response(percent=50))
    assert verdict.confidence_label is ConfidenceLabel.medium
