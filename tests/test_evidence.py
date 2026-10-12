"""Unit tests for the evidence models and source inventory (#12)."""

from __future__ import annotations

from app.diagnostics.catalog import Catalog, DiagnosticQuery
from app.evidence.models import (
    DiagnosticRunResult,
    EvidenceSource,
    QueryEvidence,
    QueryStatus,
    SourceStatus,
)
from app.evidence.registry import EvidenceRegistry, is_applicable
from app.sql.connector import Row


def _query(query_id: str, **overrides) -> DiagnosticQuery:
    values = {
        "id": query_id,
        "title": f"Title {query_id}",
        "description": "A query.",
        "category": "blocking",
        "issue_area": "blocking",
        "sql": "SELECT 1 AS one",
        "params": (),
        "requires_approval": True,
        "expected_impact": "Minimal.",
        "versions": ("2008+",),
        "min_permission": "VIEW SERVER STATE",
        "read_only": True,
    }
    values.update(overrides)
    return DiagnosticQuery(**values)


def _catalog(*queries: DiagnosticQuery) -> Catalog:
    catalog = Catalog()
    for query in queries:
        catalog.register(query)
    return catalog


def test_registry_has_one_source_per_catalog_id_in_order():
    catalog = _catalog(_query("a"), _query("b"))
    registry = EvidenceRegistry(catalog)

    assert len(registry) == 2
    assert [source.query_id for source in registry.all()] == ["a", "b"]
    assert all(
        source.status is SourceStatus.unavailable_setting for source in registry.all()
    )


def test_registry_record_updates_status_and_note():
    registry = EvidenceRegistry(_catalog(_query("a")))

    source = registry.record("a", SourceStatus.collected, "3 rows")

    assert source.status is SourceStatus.collected
    assert source.note == "3 rows"
    assert registry.get("a").status is SourceStatus.collected


def test_registry_merge_does_not_downgrade_collected():
    registry = EvidenceRegistry(_catalog(_query("a")))
    registry.record("a", SourceStatus.collected, "ok")

    registry.merge("a", SourceStatus.unavailable_connection, "later failure")

    assert registry.get("a").status is SourceStatus.collected


def test_registry_merge_accepts_weak_first_status_on_untouched_source():
    registry = EvidenceRegistry(_catalog(_query("a")))

    registry.merge("a", SourceStatus.not_applicable, "nothing retained")

    assert registry.get("a").status is SourceStatus.not_applicable


def test_applicability_predicate_only_for_known_source():
    a = _query("a")
    deadlock = _query("deadlock_system_health")

    assert is_applicable(a, []) is True
    assert is_applicable(deadlock, []) is False
    assert is_applicable(deadlock, [Row(("x",), (1,))]) is True


def test_query_evidence_defaults_and_result_shape():
    evidence = QueryEvidence(query_id="a", database="master", status=QueryStatus.ok)

    assert evidence.row_count == 0
    assert evidence.rows == []
    assert evidence.error is None
    assert evidence.truncated is False

    result = DiagnosticRunResult(
        investigation_id=7,
        evidence=[evidence],
        sources=[
            EvidenceSource(
                query_id="a",
                title="A",
                category="blocking",
                issue_area="blocking",
                status=SourceStatus.collected,
            )
        ],
    )
    assert result.status == "succeeded"
    assert result.model_dump()["investigation_id"] == 7


def test_source_status_values_match_spec():
    assert {status.value for status in SourceStatus} == {
        "collected",
        "unavailable-permission",
        "unavailable-connection",
        "unavailable-setting",
        "not-applicable",
    }
    assert {status.value for status in QueryStatus} == {
        "ok",
        "error",
        "timeout",
        "skipped",
    }
