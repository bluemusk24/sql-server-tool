"""Findings engine, cause correlator, and guidance knowledge base.

This is the analysis layer the product brief names: *summarize failures
and recommend likely corrective actions* (docs/spec.md §4–§5). It turns
collected evidence rows into findings via fixed, validated thresholds,
correlates findings into one primary probable cause plus ranked
alternatives with a confidence, and attaches recommended validation
checks and remediation guidance (with draft SQL for DBA review).

Hard rules honored here:

- **Never fabricates.** A finding may only reference rows that were
  actually collected; sources that were unavailable contribute coverage
  gaps, never signals.
- **Reports and recommends only.** Nothing here executes anything
  against a server; draft SQL is text for the DBA to review.
- **Deterministic and auditable.** Same evidence in → same analysis
  out. The "AI" of the spec is this correlation layer; an LLM
  summarizer can be added later behind a gate without changing the
  report contract.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

# ── Vocabulary ───────────────────────────────────────────────────────

#: Human titles for every issue area (the 4 original spec areas plus
#: the refresh & replication area the topic requires).
AREA_TITLES: dict[str, str] = {
    "blocking": "Blocking",
    "deadlocks": "Deadlocks",
    "queries": "Slow / resource-intensive queries",
    "cpu": "High CPU",
    "agent_jobs": "SQL Agent jobs",
    "errors": "SQL Server errors",
    "availability": "Database availability",
    "storage": "Storage pressure",
    "refresh": "Database refresh & replication",
}

#: How loudly each area speaks when ranking causes. Refresh and
#: availability sit on top: an unavailable or stale database is
#: user-visible immediately.
AREA_WEIGHTS: dict[str, int] = {
    "refresh": 5,
    "availability": 5,
    "blocking": 4,
    "storage": 4,
    "errors": 3,
    "deadlocks": 3,
    "cpu": 3,
    "agent_jobs": 2,
    "queries": 1,
}

SEVERITY_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}
_CONFIDENCE_FLOORS = ((0, "low"), (50, "medium"), (75, "high"))

_REFRESH_HINTS = (
    "refresh", "etl", "load", "ssis", "package", "dtexec", "powershell",
    "transform", "ingest", "replicat", "log shipping", "restore",
    "warehouse", "datamart", "data mart", "staging", "extract", "fact",
)

_CRITICAL_ERROR_RE = re.compile(
    r"deadlock|killed|serious|fatal|i/o error|cannot open|out of memory"
    r"|time-?out|suspect|connection was|infected|stack dump",
    re.IGNORECASE,
)

_SYSTEM_DATABASES = {"master", "model", "msdb", "tempdb"}


def confidence_label(percent: int) -> str:
    label = "low"
    for floor, name in _CONFIDENCE_FLOORS:
        if percent >= floor:
            label = name
    return label


# ── Row coercion helpers (live rows arrive as JSON-able dicts) ───────

def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _text(value: Any, limit: int = 160) -> str:
    text = "" if value is None else str(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _age_hours(value: Any, now: datetime) -> float | None:
    """Age in hours of a timestamp value (datetime or ISO string)."""
    if value is None:
        return None
    moment: datetime | None = None
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value))
        except ValueError:
            return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return max(0.0, (now - moment).total_seconds() / 3600.0)


def _looks_like_refresh(text: str) -> bool:
    lowered = text.lower()
    return any(hint in lowered for hint in _REFRESH_HINTS)


def _finding(
    source_id: str,
    kind: str,
    severity: str,
    title: str,
    description: str,
    evidence_ids: list[str],
    database: str | None = None,
) -> dict[str, Any]:
    """One rule signal. ``rule_id`` is stable so reports diff cleanly."""
    return {
        "rule_id": f"{source_id}:{kind}",
        "area": "",  # filled by derive_findings from the source
        "kind": kind,
        "severity": severity,
        "title": title,
        "description": description,
        "database": database,
        "evidence_ids": list(evidence_ids),
    }


# ── Per-source rules (spec §4 thresholds) ───────────────────────────
# Each rule receives the collected rows and returns findings; the
# caller stamps the area and maps row indexes onto evidence ids.

RuleFn = Callable[[list[dict[str, Any]], datetime], list[dict[str, Any]]]


def _rule_blocking(rows: list[dict[str, Any]], _now: datetime) -> list[dict[str, Any]]:
    findings = []
    for index, row in enumerate(rows):
        wait_ms = _num(row.get("wait_time_ms")) or 0.0
        session = row.get("blocking_session_id")
        wait_type = _text(row.get("wait_type"), 40) or "unknown wait"
        if wait_ms >= 300_000:
            severity = "high"
        elif wait_ms >= 60_000:
            severity = "medium"
        else:
            severity = "low"
        findings.append(
            _finding(
                "blocking_now",
                "blocking_chain",
                severity,
                f"Blocking chain: session {session} blocking for "
                f"{wait_ms / 60_000:.1f} min ({wait_type})",
                f"Session {session} is holding locks of type {wait_type} "
                f"for {wait_ms / 1_000:.0f}s, blocking other requests.",
                [f"blocking_now-{index}"],
            )
        )
    return findings


def _rule_deadlocks(rows: list[dict[str, Any]], _now: datetime) -> list[dict[str, Any]]:
    findings = [
        _finding(
            "deadlock_history",
            "deadlock",
            "medium",
            f"Deadlock captured in window (event {index + 1})",
            "A deadlock graph was retained by the system_health session: "
            + _text(row.get("deadlock_graph"), 120),
            [f"deadlock_history-{index}"],
        )
        for index, row in enumerate(rows)
    ]
    if len(rows) >= 3:
        findings.append(
            _finding(
                "deadlock_history",
                "deadlock_pattern",
                "high",
                f"Repeated deadlocks: {len(rows)} in the lookback window",
                "Deadlocks are recurring, not one-off — two or more "
                "transactions are racing for the same locks on a loop.",
                [f"deadlock_history-{index}" for index in range(len(rows))],
            )
        )
    return findings


def _rule_top_queries(rows: list[dict[str, Any]], _now: datetime) -> list[dict[str, Any]]:
    findings = []
    for index, row in enumerate(rows):
        cpu_ms = _num(row.get("total_cpu_ms")) or 0.0
        if cpu_ms < 10_000:
            continue  # reported as evidence; not a signal (spec §4)
        severity = "high" if cpu_ms >= 60_000 else "medium"
        executions = _num(row.get("execution_count")) or 0.0
        findings.append(
            _finding(
                "top_queries",
                "cpu_intensive_query",
                severity,
                f"Resource-intensive query: {cpu_ms / 1_000:.0f} ms CPU "
                f"across {executions:.0f} executions",
                "Query text: " + (_text(row.get("text"), 120) or "(unavailable)"),
                [f"top_queries-{index}"],
            )
        )
    return findings


def _rule_cpu_schedulers(rows: list[dict[str, Any]], _now: datetime) -> list[dict[str, Any]]:
    busy = [
        (index, _num(row.get("runnable_tasks_count")) or 0.0)
        for index, row in enumerate(rows)
        if (_num(row.get("runnable_tasks_count")) or 0.0) >= 2
    ]
    if not busy:
        return []
    peak = max(runnable for _, runnable in busy)
    severity = "high" if peak >= 8 else "medium"
    return [
        _finding(
            "cpu_schedulers",
            "cpu_saturation",
            severity,
            f"CPU saturation: {len(busy)} scheduler(s) with runnable tasks "
            f"(peak {peak:.0f} runnable)",
            "SQL Server schedulers have queued work waiting to run — a "
            "classic CPU-bound signature at the instance level.",
            [f"cpu_schedulers-{index}" for index, _ in busy],
        )
    ]


def _rule_failed_agent_jobs(rows: list[dict[str, Any]], _now: datetime) -> list[dict[str, Any]]:
    by_job: dict[str, list[int]] = {}
    messages: dict[str, str] = {}
    for index, row in enumerate(rows):
        name = _text(row.get("job_name"), 120) or "(unnamed job)"
        by_job.setdefault(name, []).append(index)
        messages.setdefault(name, _text(row.get("message"), 160))
    findings = []
    for name, indexes in sorted(by_job.items()):
        message = messages[name]
        refresh = _looks_like_refresh(f"{name} {message}")
        severity = (
            "high"
            if len(indexes) >= 2 or _CRITICAL_ERROR_RE.search(message)
            else "medium"
        )
        kind = "refresh_job_failure" if refresh else "agent_job_failure"
        repeat = f" ({len(indexes)} failed runs)" if len(indexes) > 1 else ""
        findings.append(
            _finding(
                "failed_agent_jobs",
                kind,
                severity,
                f"SQL Agent job '{name}' failed{repeat}",
                message or "No job history message retained.",
                [f"failed_agent_jobs-{index}" for index in indexes],
            )
        )
    return findings


def _rule_errors(rows: list[dict[str, Any]], _now: datetime) -> list[dict[str, Any]]:
    findings = []
    for index, row in enumerate(rows):
        text = _text(row.get("Text") or row.get("text"), 160)
        severity = "high" if _CRITICAL_ERROR_RE.search(text) else "medium"
        findings.append(
            _finding(
                "sql_server_errors",
                "server_error",
                severity,
                f"Error log entry: {text[:80]}",
                text or "(empty error log entry)",
                [f"sql_server_errors-{index}"],
            )
        )
    return findings


def _rule_database_status(rows: list[dict[str, Any]], _now: datetime) -> list[dict[str, Any]]:
    findings = []
    for index, row in enumerate(rows):
        state = _text(row.get("state_desc"), 40)
        if state.upper() == "ONLINE":
            continue
        name = _text(row.get("name"), 120) or "(unnamed database)"
        findings.append(
            _finding(
                "database_status",
                "database_unavailable",
                "high",
                f"Database {name} is {state}",
                f"{name} is not ONLINE (state: {state}) — anything "
                "depending on it fails until the state is resolved.",
                [f"database_status-{index}"],
                database=name,
            )
        )
    return findings


def _rule_storage(rows: list[dict[str, Any]], _now: datetime) -> list[dict[str, Any]]:
    findings = []
    for index, row in enumerate(rows):
        size_mb = _num(row.get("size_mb")) or 0.0
        used_mb = _num(row.get("used_mb")) or 0.0
        if size_mb <= 0:
            continue
        free_mb = size_mb - used_mb
        free_pct = free_mb / size_mb * 100.0
        if free_pct >= 10.0:
            continue
        severity = "high" if free_pct < 3.0 or free_mb < 512 else "medium"
        db_name = _text(row.get("db_name"), 120) or None
        file_name = _text(row.get("file_name"), 120) or "(file)"
        findings.append(
            _finding(
                "storage_pressure",
                "storage_pressure",
                severity,
                f"{db_name or 'instance'} · {file_name}: "
                f"{free_pct:.1f}% free ({free_mb:.0f} MB)",
                "A database file is under the 10% free-space threshold; "
                "autogrowth or an outage looms if the volume stays tight.",
                [f"storage_pressure-{index}"],
                database=db_name,
            )
        )
    return findings


def _rule_refresh_jobs(rows: list[dict[str, Any]], _now: datetime) -> list[dict[str, Any]]:
    by_job: dict[str, list[int]] = {}
    rows_by_job: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        name = _text(row.get("job_name"), 120) or "(unnamed refresh job)"
        by_job.setdefault(name, []).append(index)
        rows_by_job.setdefault(name, row)
    findings = []
    for name, indexes in sorted(by_job.items()):
        row = rows_by_job[name]
        message = _text(row.get("message"), 160)
        subsystem = _text(row.get("subsystem"), 40) or "unknown"
        step = _text(row.get("step_name"), 120) or "(step unknown)"
        severity = (
            "high"
            if subsystem in ("SSIS", "PowerShell", "CmdExec")
            or len(indexes) >= 2
            or _CRITICAL_ERROR_RE.search(message)
            else "medium"
        )
        repeat = f" ({len(indexes)} failed runs)" if len(indexes) > 1 else ""
        findings.append(
            _finding(
                "refresh_job_failures",
                "refresh_job_failure",
                severity,
                f"Refresh job '{name}' failed at step '{step}'{repeat} "
                f"[{subsystem}]",
                message or "No step error text retained.",
                [f"refresh_job_failures-{index}" for index in indexes],
            )
        )
    return findings


def _rule_replication(rows: list[dict[str, Any]], _now: datetime) -> list[dict[str, Any]]:
    findings = []
    for index, row in enumerate(rows):
        latency = _num(row.get("latency_minutes"))
        if latency is None or latency < 10:
            continue
        severity = "high" if latency >= 30 else "medium"
        subscriber = _text(row.get("subscriber_db"), 120) or None
        publication = _text(row.get("publication_name"), 120) or "(publication)"
        findings.append(
            _finding(
                "replication_latency",
                "replication_lag",
                severity,
                f"Replication lag {latency:.0f} min on '{publication}'"
                + (f" → {subscriber}" if subscriber else ""),
                "Subscribers are receiving data late — downstream copies "
                "and anything refreshing from them are stale.",
                [f"replication_latency-{index}"],
                database=subscriber,
            )
        )
    return findings


def _rule_log_shipping(rows: list[dict[str, Any]], _now: datetime) -> list[dict[str, Any]]:
    findings = []
    for index, row in enumerate(rows):
        lag = _num(row.get("restore_lag_minutes"))
        if lag is None or lag < 15:
            continue
        severity = "high" if lag >= 60 else "medium"
        secondary = _text(row.get("secondary_database"), 120) or None
        findings.append(
            _finding(
                "log_shipping_lag",
                "log_shipping_lag",
                severity,
                f"Log shipping lag {lag:.0f} min for "
                f"{secondary or 'secondary database'}",
                "The secondary copy is falling behind the primary — the "
                "standby is older than the restore threshold.",
                [f"log_shipping_lag-{index}"],
                database=secondary,
            )
        )
    return findings


def _rule_backup_freshness(rows: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    findings = []
    for index, row in enumerate(rows):
        name = _text(row.get("database_name"), 120) or None
        if name and name.lower() in _SYSTEM_DATABASES:
            continue
        last_full = row.get("last_full")
        if last_full is None:
            findings.append(
                _finding(
                    "backup_freshness",
                    "missing_backup",
                    "high",
                    f"No full backup on record for {name or 'database'}",
                    "msdb holds no full backup for this database — a "
                    "restore point may not exist at all.",
                    [f"backup_freshness-{index}"],
                    database=name,
                )
            )
            continue
        age = _age_hours(last_full, now)
        if age is None:
            continue
        days = age / 24.0
        if days <= 7.0:
            continue
        severity = "high" if days > 14.0 else "medium"
        findings.append(
            _finding(
                "backup_freshness",
                "stale_backup",
                severity,
                f"Last full backup of {name or 'database'} is "
                f"{days:.1f} days old",
                "Backup freshness is outside the 7-day expectation — "
                "recovery objectives are at risk.",
                [f"backup_freshness-{index}"],
                database=name,
            )
        )
    return findings


#: The validated rule set: one entry per catalog source id. Sources
#: without a rule still contribute evidence (and coverage gaps).
SOURCE_RULES: dict[str, RuleFn] = {
    "blocking_now": _rule_blocking,
    "deadlock_history": _rule_deadlocks,
    "top_queries": _rule_top_queries,
    "cpu_schedulers": _rule_cpu_schedulers,
    "failed_agent_jobs": _rule_failed_agent_jobs,
    "sql_server_errors": _rule_errors,
    "database_status": _rule_database_status,
    "storage_pressure": _rule_storage,
    "refresh_job_failures": _rule_refresh_jobs,
    "replication_latency": _rule_replication,
    "log_shipping_lag": _rule_log_shipping,
    "backup_freshness": _rule_backup_freshness,
}


# ── Engine entry points ──────────────────────────────────────────────

def derive_findings(
    collection: list[dict[str, Any]], *, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Run every validated rule over the collected evidence rows.

    Only ``collected`` outcomes with rows can produce findings — a gap
    never becomes a signal (spec §3.8).
    """
    moment = now or datetime.now(UTC)
    findings: list[dict[str, Any]] = []
    for outcome in collection:
        if outcome.get("status") != "collected":
            continue
        rule = SOURCE_RULES.get(outcome.get("source_id", ""))
        rows = outcome.get("evidence") or []
        if rule is None or not rows:
            continue
        for finding in rule(rows, moment):
            finding["area"] = outcome.get("issue_area", "")
            findings.append(finding)
    return findings


def build_area_skeletons(
    collection: list[dict[str, Any]], investigation_time: str
) -> list[dict[str, Any]]:
    """Group collected evidence rows by issue area.

    Shared by the live path and the synthetic demo path so both feed
    the engine the same shape. Evidence keeps its true per-source
    status; nothing is invented.
    """
    areas: dict[str, dict[str, Any]] = {}
    for outcome in collection:
        if outcome.get("status") != "collected":
            continue
        area = areas.setdefault(
            outcome["issue_area"],
            {
                "area": outcome["issue_area"],
                "severity": "none",
                "confidence_percent": 0,
                "confidence_label": "low",
                "signal_count": 0,
                "findings": [],
                "evidence": [],
            },
        )
        area["evidence"].extend(
            {
                "evidence_id": f"{outcome['source_id']}-{index}",
                "area": outcome["issue_area"],
                "timestamp": investigation_time,
                "description": outcome["title"],
                "details": row,
            }
            for index, row in enumerate(outcome.get("evidence") or [])
        )
    return list(areas.values())


# ── Cause correlator ────────────────────────────────────────────────

def _area_confidence(
    severity: str, finding_count: int, corroborating: int, gaps: int
) -> int:
    base = {"high": 65, "medium": 50, "low": 35}.get(severity, 0)
    pct = base + min(10, 2 * finding_count) + 6 * corroborating - 8 * gaps
    return max(10, min(90, int(pct)))


def _cause_for(area: dict[str, Any], findings: list[dict[str, Any]]) -> dict[str, Any]:
    guidance = GUIDANCE.get(area["area"], {})
    databases = sorted(
        {f["database"] for f in findings if f.get("database")}
    )
    where = (
        f" Affected database(s): {', '.join(databases)}." if databases else ""
    )
    return {
        "title": guidance.get("cause_title", AREA_TITLES.get(area["area"], area["area"])),
        "description": (
            f"{area['signal_count']} signal(s) in "
            f"{AREA_TITLES.get(area['area'], area['area'])}. "
            f"{guidance.get('describe', '')}{where}"
        ).strip(),
        "confidence_percent": area["confidence_percent"],
        "confidence_label": area["confidence_label"],
    }


def _rank_areas(areas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        (area for area in areas if area["severity"] != "none"),
        key=lambda area: (
            -SEVERITY_RANK[area["severity"]],
            -AREA_WEIGHTS.get(area["area"], 0),
            area["area"],
        ),
    )


def analyze(
    collection: list[dict[str, Any]],
    *,
    investigation_time: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The full analysis pass: findings → areas → cause → guidance.

    Returns the report sections the store merges into the payload:
    findings_by_area (with evidence), primary_cause, alternatives,
    confidence, severity, rationale, inconclusive, and the
    recommendations/validation/remediation guidance.
    """
    moment = now or datetime.now(UTC)
    findings = derive_findings(collection, now=moment)
    areas = build_area_skeletons(collection, investigation_time)

    by_area: dict[str, list[dict[str, Any]]] = {}
    for finding in findings:
        by_area.setdefault(finding["area"], []).append(finding)

    gaps = sum(1 for outcome in collection if outcome.get("status") != "collected")
    collected = sum(1 for outcome in collection if outcome.get("status") == "collected")

    for area in areas:
        area_findings = sorted(
            by_area.get(area["area"], []),
            key=lambda f: (-SEVERITY_RANK[f["severity"]], f["title"]),
        )
        area["findings"] = area_findings
        area["signal_count"] = len(area_findings)
        if area_findings:
            area["severity"] = max(
                (f["severity"] for f in area_findings),
                key=lambda sev: SEVERITY_RANK[sev],
            )
    areas = sorted(
        areas,
        key=lambda area: (
            -SEVERITY_RANK[area["severity"]],
            -AREA_WEIGHTS.get(area["area"], 0),
            area["area"],
        ),
    )

    ranked = _rank_areas(areas)
    corroborating = max(0, len(ranked) - 1)
    for area in ranked:
        area["confidence_percent"] = _area_confidence(
            area["severity"], area["signal_count"], corroborating, gaps
        )
        area["confidence_label"] = confidence_label(area["confidence_percent"])

    if not ranked or collected == 0:
        primary = {
            "title": "No primary cause determined",
            "description": (
                "No evidence could be collected — see coverage gaps."
                if collected == 0
                else "Evidence was collected, but no rule signals fired "
                "in the lookback window."
            ),
            "confidence_percent": 0,
            "confidence_label": "low",
        }
        report = {
            "primary_cause": primary,
            "alternatives": [],
            "confidence_percent": 0,
            "confidence_label": "low",
            "severity": "low",
            "inconclusive": True,
        }
    else:
        primary_area = ranked[0]
        primary = _cause_for(primary_area, primary_area["findings"])
        alternatives = [
            _cause_for(area, area["findings"]) for area in ranked[1:4]
        ]
        inconclusive = primary["confidence_percent"] < 50
        report = {
            "primary_cause": primary,
            "alternatives": alternatives,
            "confidence_percent": primary["confidence_percent"],
            "confidence_label": primary["confidence_label"],
            "severity": primary_area["severity"],
            "inconclusive": inconclusive,
        }

    # Guidance: one recommendation block per ranked area, from the
    # knowledge base. Text and draft SQL only — nothing is executed.
    recommendations = []
    for area in ranked:
        guidance = GUIDANCE.get(area["area"])
        if guidance is None:
            continue
        recommendations.append(
            {
                "area": area["area"],
                "validation_checks": list(guidance["validation_checks"]),
                "remediation": [
                    {
                        "step": step["step"],
                        "draft_sql": step.get("draft_sql"),
                        "disclaimer": "recommendation only, execute at the "
                        "DBA's discretion",
                    }
                    for step in guidance["remediation"]
                ],
                "disclaimer": "recommendation only, execute at the DBA's "
                "discretion",
            }
        )
    report["recommendations"] = recommendations
    report["validation_checks"] = [
        check
        for recommendation in recommendations
        for check in recommendation["validation_checks"]
    ]
    report["remediation"] = [
        step
        for recommendation in recommendations
        for step in recommendation["remediation"]
    ]

    signal_count = len(findings)
    rationale = (
        f"Live collection: {collected} of {len(collection)} sources "
        f"collected"
        + (f"; {gaps} unavailable (see coverage gaps)." if gaps else ".")
        + f" {signal_count} signal(s) across {len(ranked)} issue area(s)."
    )
    if collected and ranked:
        label = report["primary_cause"]["confidence_label"]
        rationale += (
            f" Primary cause: "
            f"{AREA_TITLES.get(ranked[0]['area'], ranked[0]['area'])} at "
            f"{report['confidence_percent']}% confidence ({label})"
            + (
                " — below the 50% threshold, treat as inconclusive."
                if report["inconclusive"]
                else "."
            )
        )
    elif collected == 0:
        rationale += " Nothing could be concluded without evidence."

    report["findings_by_area"] = areas
    report["rationale"] = rationale
    return report


# ── Guidance knowledge base (spec §5) ───────────────────────────────
# Validation checks are read-only by construction; remediation steps
# are instructions for the DBA, with draft SQL for review where it
# helps. The tool never executes any of it.

GUIDANCE: dict[str, dict[str, Any]] = {
    "refresh": {
        "cause_title": "Data refresh / replication failure",
        "describe": (
            "The refresh path is broken: a scheduled refresh job failed, "
            "replication or log shipping is delivering late, or a "
            "database's backups are stale."
        ),
        "validation_checks": [
            ("Read the failing Agent step's error text: SELECT j.name, "
            "s.step_name, s.subsystem, h.message FROM msdb.dbo.sysjobhistory "
            "h JOIN msdb.dbo.sysjobs j ON j.job_id = h.job_id JOIN "
            "msdb.dbo.sysjobsteps s ON s.job_id = h.job_id AND s.step_id = "
            "h.step_id WHERE h.run_status = 0 AND h.step_id > 0 ORDER BY "
            "h.run_date DESC, h.run_time DESC;"),
            ("Confirm replication delivery latency: SELECT name, publisher_db, "
            "subscriber_db FROM distribution.dbo.MSdistribution_agents; "
            "(requires distributor access)"),
            ("Check log-shipping restore lag: SELECT * FROM msdb.dbo."
            "log_shipping_monitor_secondary;"),
            ("Check backup freshness per database: SELECT database_name, "
            "MAX(backup_finish_date) FROM msdb.dbo.backupset GROUP BY "
            "database_name;"),
        ],
        "remediation": [
            {
                "step": "Fix the underlying data/dependency error named in "
                "the failing step, then re-run the job step from SQL Agent.",
                "draft_sql": None,
            },
            {
                "step": "For SSIS/package steps, verify the package path, "
                "stored credentials, and the server's memory grants; check "
                "the SSISDB execution report for the failing task.",
                "draft_sql": None,
            },
            {
                "step": "For replication: restart the lagging distribution "
                "agent from Replication Monitor, then confirm latency "
                "returns to normal.",
                "draft_sql": None,
            },
            {
                "step": "For stale/missing backups: re-run the missing "
                "backup for the affected database (match the recovery "
                "model) after confirming destination space.",
                "draft_sql": "-- review before running (example)\n"
                "-- BACKUP DATABASE [YourDb] TO DISK = "
                "N'\\\\backup\\share\\YourDb.bak' WITH INIT, COMPRESSION;",
            },
        ],
    },
    "blocking": {
        "cause_title": "Blocking chain preventing query completion",
        "describe": (
            "A head-blocker session is holding locks long enough to queue "
            "other requests behind it."
        ),
        "validation_checks": [
            ("Re-check current blockers: SELECT blocking_session_id, "
            "wait_type, wait_time_ms, session_id FROM sys.dm_exec_requests "
            "WHERE blocking_session_id <> 0;"),
            ("See what the head blocker is running: SELECT * FROM "
            "sys.dm_exec_input_buffer(<blocking_session_id>);"),
        ],
        "remediation": [
            {
                "step": "Identify the head blocker's open transaction "
                "(often an uncommitted batch or a missing WHERE clause) and "
                "end it deliberately if it is stuck.",
                "draft_sql": "-- review before running\n"
                "-- KILL <blocking_session_id>;",
            },
            {
                "step": "Shorten the blocker: add missing indexes, split the "
                "batch, or move the operation to an off-peak window.",
                "draft_sql": None,
            },
        ],
    },
    "deadlocks": {
        "cause_title": "Deadlocks between competing transactions",
        "describe": (
            "Two or more transactions are requesting the same locks in "
            "opposite orders; SQL Server is killing one as the victim."
        ),
        "validation_checks": [
            ("Read the retained deadlock graph from the system_health "
            "session and identify the victim and the dominant resources."),
            ("Compare the two transactions' query plans for lock-escalation "
            "or wide scans."),
        ],
        "remediation": [
            {
                "step": "Make the transactions acquire locks in a "
                "consistent order (same table/index sequence in every "
                "code path).",
                "draft_sql": None,
            },
            {
                "step": "Shorten lock hold time: keep transactions small, "
                "avoid user interaction inside them, and consider "
                "READ_COMMITTED_SNAPSHOT if blocking-compatible.",
                "draft_sql": None,
            },
        ],
    },
    "queries": {
        "cause_title": "Resource-intensive queries dominating load",
        "describe": (
            "A small set of queries is consuming most of the instance's "
            "CPU or reads."
        ),
        "validation_checks": [
            ("Capture the actual plan for the top query and look for scans, "
            "spills, or parameter-sensitive plans."),
            ("Check whether the same query text dominates current requests: "
            "SELECT TOP 5 * FROM sys.dm_exec_requests WHERE status = "
            "'running';"),
        ],
        "remediation": [
            {
                "step": "Tune the query: add or fix indexes for the dominant "
                "access path, and review parameters/sniffing.",
                "draft_sql": None,
            },
            {
                "step": "Contain the blast radius: Resource Governor "
                "workload group or an approved query timeout for the "
                "offending batch.",
                "draft_sql": None,
            },
        ],
    },
    "cpu": {
        "cause_title": "CPU / scheduler saturation",
        "describe": (
            "SQL Server schedulers have runnable tasks queued — the "
            "instance is CPU-bound."
        ),
        "validation_checks": [
            ("Identify the top CPU consumers: pair this report's top "
            "resource-consuming queries with scheduler pressure."),
            ("Check signal waits vs resource waits: SELECT * FROM "
            "sys.dm_os_wait_stats WHERE wait_type = 'SOS_SCHEDULER_YIELD';"),
        ],
        "remediation": [
            {
                "step": "Reduce CPU demand: tune the top queries, eliminate "
                "compilation storms (parameterize ad-hoc SQL), and review "
                "memory grants for spills.",
                "draft_sql": None,
            },
        ],
    },
    "agent_jobs": {
        "cause_title": "Failed SQL Agent job(s)",
        "describe": (
            "One or more scheduled jobs failed in the window — check "
            "whether they are refresh/ETL jobs (see the refresh area) or "
            "maintenance jobs."
        ),
        "validation_checks": [
            ("Read the failing step's error output: SELECT j.name, s.step_id, "
            "s.step_name, h.message FROM msdb.dbo.sysjobhistory h JOIN "
            "msdb.dbo.sysjobs j ON j.job_id = h.job_id JOIN msdb.dbo."
            "sysjobsteps s ON s.job_id = h.job_id AND s.step_id = h.step_id "
            "WHERE h.run_status = 0;"),
        ],
        "remediation": [
            {
                "step": "Fix the failing step's cause (permissions, path, "
                "disk, dependency), then re-run the job; add alerting on "
                "job failure so the next one pages someone.",
                "draft_sql": None,
            },
        ],
    },
    "errors": {
        "cause_title": "SQL Server error-log entries in window",
        "describe": (
            "The error log recorded error-level entries during the window — "
            "they often explain failures elsewhere in this report."
        ),
        "validation_checks": [
            "Read adjacent error logs for context: EXEC sp_readerrorlog 1;",
            ("Correlate entry timestamps with job history and the incident "
            "timeline."),
        ],
        "remediation": [
            {
                "step": "Address the underlying fault (I/O, permissions, "
                "resource exhaustion) named in the entries, and set up "
                "SQL Server Alerts for the recurring error numbers.",
                "draft_sql": None,
            },
        ],
    },
    "availability": {
        "cause_title": "Database unavailable (offline / suspect)",
        "describe": (
            "A database is not ONLINE — everything that depends on it "
            "fails until the state is resolved."
        ),
        "validation_checks": [
            ("Inspect database states and flags: SELECT name, state_desc, "
            "user_access_desc, is_in_standby FROM sys.databases;"),
            ("For a SUSPECT database, check the SQL error log and the "
            "underlying volume before touching the database."),
        ],
        "remediation": [
            {
                "step": "For an OFFLINE database that should be serving: "
                "bring it back online after confirming the files exist.",
                "draft_sql": "-- review before running\n"
                "-- ALTER DATABASE [YourDb] SET ONLINE;",
            },
            {
                "step": "For a SUSPECT database: follow the documented "
                "emergency recovery path (EMERGENCY mode + DBCC CHECKDB) "
                "with a restore plan ready — involve a second DBA.",
                "draft_sql": "-- review before running\n"
                "-- ALTER DATABASE [YourDb] SET EMERGENCY;\n"
                "-- DBCC CHECKDB ([YourDb], REPAIR_ALLOW_DATA_LOSS);",
            },
        ],
    },
    "storage": {
        "cause_title": "Storage pressure (<10% free)",
        "describe": (
            "A database file is under the 10% free-space threshold; "
            "autogrowth will hit the volume or fail."
        ),
        "validation_checks": [
            ("Check file sizes and free space: SELECT DB_NAME(database_id), "
            "name, size * 8 / 1024 AS size_mb FROM sys.master_files;"),
            "Confirm the volume behind the file has room to grow.",
        ],
        "remediation": [
            {
                "step": "Free or add space: grow the file/volume now if the "
                "trend continues, archive old data, and revisit autogrowth "
                "increments (avoid tiny autogrowth steps).",
                "draft_sql": "-- review before running (example: +1 GB)\n"
                "-- ALTER DATABASE [YourDb] MODIFY FILE "
                "(NAME = N'YourDb', SIZE = 1048576MB);",
            },
        ],
    },
}
