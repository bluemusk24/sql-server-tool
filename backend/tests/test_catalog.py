"""Tests for the read-only diagnostic query catalog.

Covers the model + registry (duplicate-key rejection, full list, audit), the
read-only SQL guard, the no-interpolation check, the shipped query data (all
four evidence categories / eight issue areas, params metadata), JSON loading
(duplicate id, non-read-only entry), and the ``GET /catalog`` endpoints.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from app.diagnostics import queries as queries_package
from app.diagnostics.catalog import (
    EVIDENCE_CATEGORIES,
    FORBIDDEN_STATEMENT_WORDS,
    ISSUE_AREAS,
    Catalog,
    CatalogError,
    CatalogValidationError,
    DiagnosticQuery,
    DuplicateQueryError,
    ReadOnlyQueryError,
    assert_no_interpolation,
    assert_read_only,
    build_catalog,
    load_catalog,
)

VALID_ENV = {
    "DATABASE_URL": "mssql+pyodbc://user:pass@db:1433/repo?driver=ODBC+Driver+18+for+SQL+Server",
    "REDIS_URL": "redis://localhost:6379/0",
    "OIDC_ISSUER": "https://issuer.example.com/",
    "OIDC_CLIENT_ID": "client",
    "OIDC_CLIENT_SECRET": "client-secret",
    "LLM_ENDPOINT": "https://llm.example.com/v1",
    "LLM_API_KEY": "llm-key",
}


def _query(**overrides) -> DiagnosticQuery:
    values = {
        "id": "sample_query",
        "title": "Sample query",
        "description": "A sample read-only query.",
        "category": "blocking",
        "issue_area": "blocking",
        "sql": "SELECT session_id FROM sys.dm_exec_requests",
        "params": (),
        "requires_approval": True,
        "expected_impact": "Minimal.",
        "versions": ("2008+",),
        "min_permission": "VIEW SERVER STATE",
        "read_only": True,
    }
    values.update(overrides)
    return DiagnosticQuery(**values)


def _entry(**overrides) -> dict:
    entry = _query().to_dict()
    entry.update(overrides)
    return entry


# --- model ------------------------------------------------------------------


def test_query_is_a_read_only_dataclass():
    import dataclasses

    query = _query()
    assert dataclasses.is_dataclass(DiagnosticQuery)
    assert query.read_only is True
    assert query.key == query.id
    assert query.sql_text == query.sql
    assert query.purpose == query.description


def test_read_only_false_is_rejected_with_the_query_id():
    with pytest.raises(CatalogValidationError, match="not_read_only|read_only must be True"):
        _query(id="not_read_only", read_only=False)


def test_unknown_issue_area_and_category_pairing_is_rejected():
    with pytest.raises(CatalogValidationError, match="does not belong"):
        _query(category="blocking", issue_area="storage")
    with pytest.raises(CatalogValidationError, match="unknown category"):
        _query(category="nope", issue_area="blocking")


def test_sql_must_start_with_select_and_be_non_empty():
    with pytest.raises(CatalogValidationError, match="start with SELECT"):
        _query(sql="EXEC sp_who")
    with pytest.raises(CatalogValidationError, match="sql is required"):
        _query(sql="   ")


# --- registry ---------------------------------------------------------------


def test_registering_duplicate_key_is_rejected():
    catalog = Catalog()
    catalog.register(_query())
    with pytest.raises(DuplicateQueryError, match="sample_query"):
        catalog.register(_query())


def test_registry_returns_the_full_list_with_unique_ids():
    catalog = build_catalog()
    ids = [query.id for query in catalog.all()]
    assert len(ids) == len(set(ids)) == len(catalog) >= 8
    assert catalog.all() == list(catalog)
    assert catalog.get(ids[0]) is not None
    assert catalog.get("missing") is None


def test_registry_audit_reports_every_query_with_its_flags():
    catalog = build_catalog()
    report = catalog.audit()

    assert len(report) == len(catalog)
    assert {row["id"] for row in report} == {query.id for query in catalog.all()}
    assert all(row["read_only"] is True for row in report)
    assert all(row["requires_approval"] is True for row in report)


# --- coverage ---------------------------------------------------------------


def test_registry_covers_all_four_evidence_categories():
    covered = {query.category for query in build_catalog().all()}
    assert covered == set(EVIDENCE_CATEGORIES)


def test_registry_covers_every_issue_area():
    covered = {query.issue_area for query in build_catalog().all()}
    assert covered == set(ISSUE_AREAS)


# --- read-only guard --------------------------------------------------------


@pytest.mark.parametrize("keyword", sorted(FORBIDDEN_STATEMENT_WORDS))
def test_guard_rejects_each_write_keyword(keyword):
    with pytest.raises(ReadOnlyQueryError, match=keyword):
        assert_read_only(f"SELECT 1\n{keyword} something", query_id="probe")


def test_guard_allows_select_and_ignores_keywords_in_strings_and_identifiers():
    sql = (
        "SELECT [update] AS the_column, 'DROP TABLE x' AS note, N'-- insert'\n"
        "FROM sys.dm_exec_requests"
    )

    assert_read_only(sql)  # does not raise


def test_guard_rejects_empty_sql():
    with pytest.raises(ReadOnlyQueryError, match="non-empty"):
        assert_read_only("   ")


# --- interpolation ----------------------------------------------------------


def test_no_interpolation_rejects_braces_and_concatenation():
    with pytest.raises(CatalogValidationError, match="braces"):
        assert_no_interpolation("SELECT * FROM {table}")
    with pytest.raises(CatalogValidationError, match="concatenation"):
        assert_no_interpolation("SELECT 'a' || 'b'")


def test_no_interpolation_allows_parameters():
    assert_no_interpolation("SELECT * FROM sys.tables WHERE name = ?")  # does not raise


def test_shipped_sql_contains_no_interpolation():
    for query in build_catalog().all():
        assert_no_interpolation(query.sql, query_id=query.id)
        assert "{" not in query.sql and "}" not in query.sql
        assert "||" not in query.sql


def test_query_definition_sources_do_not_build_sql_with_interpolation():
    source_dir = Path(queries_package.__file__).parent
    pattern = re.compile(r"""\bf["']|\.format\(""")
    for source_file in source_dir.glob("*.py"):
        source = source_file.read_text(encoding="utf-8")
        assert not pattern.search(source), f"{source_file.name} builds SQL via interpolation"


# --- shipped data -----------------------------------------------------------


def test_every_shipped_query_passes_the_read_only_guard():
    for query in build_catalog().all():
        assert_read_only(query.sql, query_id=query.id)
        assert query.sql.lstrip().upper().startswith("SELECT")
        assert query.read_only is True


def test_every_query_has_params_metadata_matching_placeholders():
    for query in build_catalog().all():
        assert isinstance(query.params, tuple)
        assert all(isinstance(name, str) and name for name in query.params)
        assert len(query.params) == query.sql.count("?")


def test_catalog_json_matches_the_code_definitions():
    loaded = load_catalog()
    built = build_catalog()

    assert [query.id for query in loaded.all()] == [query.id for query in built.all()]
    assert [query.to_dict() for query in loaded.all()] == [query.to_dict() for query in built.all()]


# --- load_catalog -----------------------------------------------------------


def test_load_catalog_default_path_is_valid():
    catalog = load_catalog()
    assert len(catalog) >= 8
    assert all(query.read_only for query in catalog.all())


def test_entry_with_read_only_false_fails_load(tmp_path):
    entry = _entry(id="bad_read_only", read_only=False)
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps([entry]), encoding="utf-8")

    with pytest.raises(CatalogValidationError, match="bad_read_only"):
        load_catalog(path)


@pytest.mark.parametrize(
    "field",
    [
        "title",
        "description",
        "category",
        "issue_area",
        "sql",
        "expected_impact",
        "versions",
        "min_permission",
        "read_only",
    ],
)
def test_each_required_field_missing_fails_load(tmp_path, field):
    entry = _entry(id="missing_field")
    entry.pop(field)
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps([entry]), encoding="utf-8")

    with pytest.raises(CatalogValidationError, match="missing_field"):
        load_catalog(path)


def test_duplicate_id_fails_load(tmp_path):
    entry = _entry(id="dupe")
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps([entry, entry]), encoding="utf-8")

    with pytest.raises(CatalogValidationError, match="dupe"):
        load_catalog(path)


def test_catalog_file_must_be_a_json_array(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps({"id": "x"}), encoding="utf-8")

    with pytest.raises(CatalogError, match="JSON array"):
        load_catalog(path)


# --- endpoints --------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    for name, value in VALID_ENV.items():
        monkeypatch.setenv(name, value)
    sys.modules.pop("app.main", None)
    from app.main import create_app

    return TestClient(create_app(_settings()))


def _settings():
    from app.config import Settings

    return Settings(
        database_url=VALID_ENV["DATABASE_URL"],
        redis_url=VALID_ENV["REDIS_URL"],
        oidc_issuer=VALID_ENV["OIDC_ISSUER"],
        oidc_client_id=VALID_ENV["OIDC_CLIENT_ID"],
        oidc_client_secret=VALID_ENV["OIDC_CLIENT_SECRET"],
        llm_endpoint=VALID_ENV["LLM_ENDPOINT"],
        llm_api_key=VALID_ENV["LLM_API_KEY"],
    )


def test_get_catalog_returns_full_ordered_list_with_verbatim_sql(client):
    response = client.get("/catalog")
    assert response.status_code == 200

    entries = response.json()
    built = build_catalog().all()
    assert [entry["id"] for entry in entries] == [query.id for query in built]
    assert {entry["issue_area"] for entry in entries} == set(ISSUE_AREAS)
    assert all(entry["read_only"] is True for entry in entries)

    by_id = {entry["id"]: entry for entry in entries}
    assert by_id[built[0].id]["sql_text"] == built[0].sql
    assert by_id[built[0].id]["sql"] == built[0].sql


def test_get_catalog_entry_returns_one_entry(client):
    response = client.get("/catalog/blocking_current_chains")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "blocking_current_chains"
    assert body["read_only"] is True


def test_get_catalog_unknown_id_returns_404(client):
    response = client.get("/catalog/nope")
    assert response.status_code == 404
