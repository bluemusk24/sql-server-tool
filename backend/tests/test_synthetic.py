"""Tests for the synthetic report generator (#26).

Deterministic given a seed, schema-identical to the demo report the
whole product already consumes, and unmistakably labeled as sample
data — never evidence.
"""

from __future__ import annotations

import json

from app.models import ReportPayload
from app.store import CATALOG, Investigation, build_demo_report
from app.synthetic import SYNTHETIC_LABEL, generate_synthetic_report, main


def _strip_time(payload):
    """Drop every wall-clock value: investigation_time and the evidence
    timestamps that embed it (they'd differ between two calls a second
    apart even with an identical seed)."""
    if isinstance(payload, dict):
        return {
            key: _strip_time(value)
            for key, value in payload.items()
            if key not in ("investigation_time", "timestamp")
        }
    if isinstance(payload, list):
        return [_strip_time(value) for value in payload]
    return payload


def _inv(inv_id: int = 7) -> Investigation:
    return Investigation(
        id=inv_id,
        status="completed",
        server_host="sql-synth.example",
        server_port=1433,
        auth_type="sql",
        databases=None,
        lookback_hours=4,
        created_at="2026-01-01T00:00:00Z",
        catalog=list(CATALOG),
    )


def test_same_seed_produces_the_same_report() -> None:
    first = generate_synthetic_report(_inv(7), "Acme Corp", seed=42)
    second = generate_synthetic_report(_inv(7), "Acme Corp", seed=42)
    assert _strip_time(first) == _strip_time(second)


def test_the_investigation_id_is_the_default_seed() -> None:
    by_id = generate_synthetic_report(_inv(9), "Acme Corp")
    explicit = generate_synthetic_report(_inv(99), "Acme Corp", seed=9)
    # Same seed → identical sample; only the identity fields differ.
    by_id.pop("investigation_id", None)
    explicit.pop("investigation_id", None)
    assert _strip_time(by_id) == _strip_time(explicit)


def test_a_different_seed_differs() -> None:
    first = generate_synthetic_report(_inv(7), "Acme Corp", seed=1)
    second = generate_synthetic_report(_inv(7), "Acme Corp", seed=2)
    assert first != second


def test_the_payload_matches_the_report_schema() -> None:
    payload = generate_synthetic_report(_inv(7), "Acme Corp", seed=42)
    # Same shape the demo builder has always produced, plus the label.
    assert set(payload) == set(build_demo_report(_inv(7), "Acme Corp")) | {
        "synthetic"
    }
    # And it satisfies the API's report model.
    ReportPayload(**payload)


def test_every_issue_area_is_represented() -> None:
    payload = generate_synthetic_report(_inv(7), "Acme Corp", seed=42)
    catalog_areas = {entry.issue_area for entry in CATALOG}
    report_areas = {area["area"] for area in payload["findings_by_area"]}
    assert report_areas == catalog_areas
    # Every area carries evidence rows; findings exist outside quiet areas.
    assert all(area["evidence"] for area in payload["findings_by_area"])
    assert any(area["findings"] for area in payload["findings_by_area"])
    for area in payload["findings_by_area"]:
        for finding in area["findings"]:
            assert finding["rule_id"].startswith("synthetic-")
            assert finding["severity"] in ("high", "medium", "low")
            assert finding["evidence_ids"]


def test_the_report_is_labeled_synthetic() -> None:
    payload = generate_synthetic_report(_inv(7), "Acme Corp", seed=42)
    assert payload["synthetic"] is True
    assert payload["rationale"].startswith(SYNTHETIC_LABEL)
    assert "SYNTHETIC SAMPLE DATA" in payload["rationale"]


def test_the_cli_prints_a_sample_report(capsys) -> None:
    assert main(["--seed", "5"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["synthetic"] is True
    assert payload["investigation_id"] == 5
    assert payload["findings_by_area"]
