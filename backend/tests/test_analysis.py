"""Tests for the findings engine, cause correlator, and guidance (#49).

The analysis layer is the product's core promise: turn collected
evidence into findings, a primary probable cause with a confidence,
and corrective guidance — never fabricating a signal from a gap.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.analysis import (
    AREA_TITLES,
    GUIDANCE,
    SOURCE_RULES,
    analyze,
    build_area_skeletons,
    derive_findings,
)

NOW = datetime(2026, 10, 19, 12, 0, 0, tzinfo=UTC)
T = "2026-10-19T11:00:00+00:00"


def _outcome(source_id: str, area: str, evidence, status: str = "collected"):
    return {
        "source_id": source_id,
        "title": f"{source_id} title",
        "category": area,
        "issue_area": area,
        "status": status,
        "note": "",
        "evidence": evidence,
        "row_count": len(evidence),
    }


# ── derive_findings: rules over rows ────────────────────────────────

def test_blocking_rules_grade_by_wait_time() -> None:
    collection = [
        _outcome(
            "blocking_now",
            "blocking",
            [
                {"blocking_session_id": 71, "wait_type": "LCK_M_X", "wait_time_ms": 900_000},
                {"blocking_session_id": 72, "wait_type": "LCK_M_S", "wait_time_ms": 30_000},
            ],
        )
    ]
    findings = derive_findings(collection, now=NOW)
    assert [f["severity"] for f in findings] == ["high", "low"]
    assert findings[0]["evidence_ids"] == ["blocking_now-0"]
    assert findings[0]["area"] == "blocking"
    assert "session 71" in findings[0]["title"]


def test_storage_rules_flag_under_ten_percent_and_attach_the_database() -> None:
    collection = [
        _outcome(
            "storage_pressure",
            "storage",
            [
                {"db_name": "Sales", "file_name": "Sales", "size_mb": 10_000, "used_mb": 9_900},
                {"db_name": "Archive", "file_name": "Archive", "size_mb": 100_000, "used_mb": 92_000},
                {"db_name": "Orders", "file_name": "Orders", "size_mb": 10_000, "used_mb": 5_000},
            ],
        )
    ]
    findings = derive_findings(collection, now=NOW)
    by_db = {f["database"]: f["severity"] for f in findings}
    assert by_db == {"Sales": "high", "Archive": "medium"}  # Orders: healthy
    assert findings[0]["evidence_ids"] == ["storage_pressure-0"]


def test_availability_flags_any_non_online_database() -> None:
    collection = [
        _outcome(
            "database_status",
            "availability",
            [
                {"name": "Sales", "state_desc": "ONLINE"},
                {"name": "Orders", "state_desc": "SUSPECT"},
            ],
        )
    ]
    findings = derive_findings(collection, now=NOW)
    assert len(findings) == 1
    assert findings[0]["severity"] == "high"
    assert findings[0]["database"] == "Orders"


def test_top_queries_only_fire_above_threshold() -> None:
    collection = [
        _outcome(
            "top_queries",
            "queries",
            [
                {"execution_count": 5, "total_cpu_ms": 120_000, "total_logical_reads": 10, "text": "SELECT 1"},
                {"execution_count": 5, "total_cpu_ms": 500, "total_logical_reads": 10, "text": "SELECT 2"},
            ],
        )
    ]
    findings = derive_findings(collection, now=NOW)
    assert len(findings) == 1  # the cheap query stays plain evidence
    assert findings[0]["severity"] == "high"


def test_agent_job_failures_classify_refresh_jobs_and_repeats() -> None:
    collection = [
        _outcome(
            "failed_agent_jobs",
            "agent_jobs",
            [
                {"job_name": "Nightly DW Refresh", "run_status": 0, "message": "SSIS error"},
                {"job_name": "Nightly DW Refresh", "run_status": 0, "message": "SSIS error"},
                {"job_name": "Index Rebuild", "run_status": 0, "message": "ok-ish"},
            ],
        )
    ]
    findings = derive_findings(collection, now=NOW)
    refresh = next(f for f in findings if "DW Refresh" in f["title"])
    maintenance = next(f for f in findings if "Index Rebuild" in f["title"])
    assert refresh["kind"] == "refresh_job_failure"
    assert refresh["severity"] == "high"  # repeated failure
    assert "2 failed runs" in refresh["title"]
    assert maintenance["kind"] == "agent_job_failure"
    assert maintenance["severity"] == "medium"


# ── refresh-area rules (the topic's headline) ───────────────────────

def test_every_catalog_source_has_a_validated_rule() -> None:
    from app.store import CATALOG

    missing = [entry.id for entry in CATALOG if entry.id not in SOURCE_RULES]
    assert missing == []


def test_refresh_job_failures_flag_ssIS_steps() -> None:
    collection = [
        _outcome(
            "refresh_job_failures",
            "refresh",
            [
                {
                    "job_name": "DW Refresh",
                    "step_name": "Load FactSales",
                    "step_id": 2,
                    "subsystem": "SSIS",
                    "run_status": 0,
                    "message": "Error: The data flow task failed.",
                }
            ],
        )
    ]
    findings = derive_findings(collection, now=NOW)
    assert len(findings) == 1
    assert findings[0]["severity"] == "high"
    assert findings[0]["area"] == "refresh"


def test_replication_latency_thresholds_and_database() -> None:
    collection = [
        _outcome(
            "replication_latency",
            "refresh",
            [
                {"publication_name": "SalesPub", "subscriber_db": "SalesReplica", "latency_minutes": 45},
                {"publication_name": "FastPub", "subscriber_db": "FastReplica", "latency_minutes": 2},
            ],
        )
    ]
    findings = derive_findings(collection, now=NOW)
    assert len(findings) == 1
    assert findings[0]["severity"] == "high"
    assert findings[0]["database"] == "SalesReplica"


def test_log_shipping_lag_severity_scales_with_lag() -> None:
    collection = [
        _outcome(
            "log_shipping_lag",
            "refresh",
            [
                {"secondary_database": "Reporting", "restore_lag_minutes": 90},
                {"secondary_database": "Archive", "restore_lag_minutes": 20},
            ],
        )
    ]
    findings = derive_findings(collection, now=NOW)
    by_db = {f["database"]: f["severity"] for f in findings}
    assert by_db == {"Reporting": "high", "Archive": "medium"}


def test_backup_freshness_flags_missing_and_stale_backups() -> None:
    collection = [
        _outcome(
            "backup_freshness",
            "refresh",
            [
                {"database_name": "Sales", "last_full": None},
                {"database_name": "Orders", "last_full": "2026-10-01T02:00:00+00:00"},  # 18 days
                {"database_name": "Fresh", "last_full": "2026-10-18T02:00:00+00:00"},  # 1 day
                {"database_name": "master", "last_full": None},  # system db: skipped
            ],
        )
    ]
    findings = derive_findings(collection, now=NOW)
    titles = {f["database"]: f for f in findings}
    assert titles["Sales"]["kind"] == "missing_backup"
    assert titles["Sales"]["severity"] == "high"
    assert titles["Orders"]["severity"] == "high"  # > 14 days
    assert "Fresh" not in titles
    assert "master" not in titles


# ── honesty: gaps never become findings ─────────────────────────────

def test_unavailable_sources_produce_no_findings() -> None:
    collection = [
        _outcome("blocking_now", "blocking", [], status="unavailable"),
        _outcome("storage_pressure", "storage", [], status="unavailable"),
    ]
    assert derive_findings(collection, now=NOW) == []
    result = analyze(collection, investigation_time=T, now=NOW)
    assert result["findings_by_area"] == []
    assert result["inconclusive"] is True
    assert result["confidence_percent"] == 0
    assert result["primary_cause"]["title"] == "No primary cause determined"


# ── correlate: areas, primary cause, confidence, guidance ───────────

def _mixed_collection():
    return [
        _outcome(
            "refresh_job_failures",
            "refresh",
            [
                {
                    "job_name": "DW Refresh",
                    "step_name": "Load",
                    "step_id": 1,
                    "subsystem": "SSIS",
                    "message": "failed",
                }
            ],
        ),
        _outcome(
            "database_status",
            "availability",
            [{"name": "Orders", "state_desc": "OFFLINE"}],
        ),
        _outcome("cpu_schedulers", "cpu", [{"scheduler_id": 1, "runnable_tasks_count": 0}]),
        _outcome("storage_pressure", "storage", [], status="unavailable"),
    ]


def test_correlate_picks_the_loudest_area_and_ranks_alternatives() -> None:
    result = analyze(_mixed_collection(), investigation_time=T, now=NOW)
    # Availability and refresh are both high at equal weight; the
    # deterministic tie-break (area name) puts the offline database
    # first — an offline database can itself break the refresh.
    assert result["primary_cause"]["title"] == (
        "Database unavailable (offline / suspect)"
    )
    alternative_titles = [alt["title"] for alt in result["alternatives"]]
    assert "Data refresh / replication failure" in alternative_titles
    # Areas are ordered by severity then weight; the quiet cpu area is
    # reported as evidence but is not a cause candidate.
    areas = [area["area"] for area in result["findings_by_area"]]
    assert areas[0] == "availability"
    assert "cpu" in areas  # evidence still reported, severity none
    cpu = next(a for a in result["findings_by_area"] if a["area"] == "cpu")
    assert cpu["severity"] == "none"
    assert cpu["findings"] == []


def test_coverage_gaps_lower_confidence_and_are_rationale_visible() -> None:
    full = analyze(_mixed_collection()[:3], investigation_time=T, now=NOW)
    gapped = analyze(_mixed_collection(), investigation_time=T, now=NOW)
    assert gapped["confidence_percent"] < full["confidence_percent"]
    assert "1 unavailable" in gapped["rationale"]
    assert "coverage gaps" in gapped["rationale"]


def test_confidence_labels_and_inconclusive_threshold() -> None:
    result = analyze(_mixed_collection(), investigation_time=T, now=NOW)
    assert result["confidence_label"] in ("low", "medium", "high")
    # High-severity primary + corroboration must land >= 50 (not inconclusive).
    assert result["inconclusive"] is False
    assert result["confidence_percent"] >= 50


def test_guidance_exists_for_every_area_and_is_review_only() -> None:
    assert set(GUIDANCE) == set(AREA_TITLES)
    for guidance in GUIDANCE.values():
        assert guidance["cause_title"]
        assert guidance["validation_checks"]
        assert guidance["remediation"]
        for step in guidance["remediation"]:
            assert step["step"]
            assert "draft_sql" in step
    result = analyze(_mixed_collection(), investigation_time=T, now=NOW)
    assert result["validation_checks"]
    assert result["remediation"]
    assert all(
        "disclaimer" in step for step in result["remediation"]
    )


def test_analyze_annotates_databases_on_the_primary_cause() -> None:
    result = analyze(_mixed_collection(), investigation_time=T, now=NOW)
    # The offline database is the primary cause and names itself.
    assert "Orders" in result["primary_cause"]["description"]
    # The refresh alternative carries the refresh job's scope (none).
    refresh = next(
        alt
        for alt in result["alternatives"]
        if alt["title"].startswith("Data refresh")
    )
    assert "signal(s)" in refresh["description"]


# ── build_area_skeletons keeps evidence intact ──────────────────────

def test_skeletons_group_evidence_by_area_with_stable_ids() -> None:
    skeletons = build_area_skeletons(_mixed_collection(), T)
    refresh = next(a for a in skeletons if a["area"] == "refresh")
    assert refresh["evidence"][0]["evidence_id"] == "refresh_job_failures-0"
    assert refresh["evidence"][0]["timestamp"] == T
    # Unavailable sources contribute no area skeleton (they are gaps).
    assert {a["area"] for a in skeletons} == {"refresh", "availability", "cpu"}
