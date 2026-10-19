"""Deterministic synthetic evidence for the demo pipeline (#26, #50).

Generates realistic-shaped *evidence rows* per catalog source — then
runs the very same analysis engine (``app.analysis``) the live path
uses, so the demo exercises the product's real rules, correlator, and
guidance. Everything it emits is unmistakably labeled as sample data —
never evidence (spec §3.8: nothing unverified is ever presented as
real).

Deterministic by seed: the same seed always produces the same report.
The demo path (a run without credentials) derives the seed from the
investigation id, so a given investigation's synthetic report is stable
across renders and restarts.

CLI: ``python -m app.synthetic [--seed N]`` prints a sample report as
JSON on stdout, for fixtures and frontend development.
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app import analysis

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.store import Investigation

#: Carried on every generated payload, and repeated in the rationale in
#: plain words — nobody can mistake sample data for collected evidence.
SYNTHETIC_LABEL = (
    "SYNTHETIC SAMPLE DATA — not collected from any real server."
)

#: Fixed clock for age math so a given seed always classifies backups
#: and lag the same way, regardless of when the report is rendered.
_SYNTHETIC_NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


def _iso(days_ago: float = 0.0) -> str:
    from datetime import timedelta

    return (_SYNTHETIC_NOW - timedelta(days=days_ago)).isoformat()


# ── Row builders: (rng) -> rows, one per catalog source ────────────
# Each builder can produce interesting rows (rules fire) or benign
# rows (rules stay quiet). The report picks per area, deterministically.

def _blocking_rows(rng: random.Random, quiet: bool) -> list[dict[str, Any]]:
    if quiet:
        return []
    return [
        {
            "blocking_session_id": rng.randint(50, 200),
            "wait_type": "LCK_M_X",
            "wait_time_ms": rng.randint(6, 15) * 60_000,
        }
    ]


def _deadlock_rows(rng: random.Random, quiet: bool) -> list[dict[str, Any]]:
    count = 0 if quiet else rng.randint(1, 3)
    return [
        {
            "deadlock_graph": (
                "<deadlock><victim process=\"spid\"/>"
                "<process-list><process id=\"spid\" "
                "waitresource=\"KEY: 5:72057594038876160\"/></process-list>"
                "</deadlock>"
            )
        }
        for _ in range(count)
    ]


def _top_query_rows(rng: random.Random, quiet: bool) -> list[dict[str, Any]]:
    cpu = 400 if quiet else rng.randint(12, 120) * 1_000
    return [
        {
            "execution_count": rng.randint(10, 5_000),
            "total_cpu_ms": cpu,
            "total_logical_reads": rng.randint(1_000, 9_000_000),
            "text": "SELECT c.CustomerId, o.OrderTotal FROM dbo.Customers c "
            "JOIN dbo.Orders o ON o.CustomerId = c.CustomerId;",
        }
    ]


def _cpu_rows(rng: random.Random, quiet: bool) -> list[dict[str, Any]]:
    peak = 0 if quiet else rng.randint(2, 9)
    return [
        {
            "scheduler_id": scheduler_id,
            "current_tasks_count": rng.randint(0, 12),
            "runnable_tasks_count": rng.randint(0, peak),
            "current_workers_count": rng.randint(0, 16),
        }
        for scheduler_id in range(4)
    ]


def _agent_job_rows(rng: random.Random, quiet: bool) -> list[dict[str, Any]]:
    if quiet:
        return []  # no failed runs in the window
    failures = rng.randint(1, 2)
    return [
        {
            "job_name": "Nightly DW Refresh",
            "run_status": 0,
            "message": "Error: The final step in job 'Nightly DW Refresh' "
            "reported outcome C on behalf of job step 2 (Load FactSales).",
        }
        for _ in range(failures)
    ]


def _error_rows(rng: random.Random, quiet: bool) -> list[dict[str, Any]]:
    if quiet:
        return [
            {
                "LogDate": _iso(),
                "ProcessInfo": "spid51",
                "Text": "Microsoft SQL Server 2022 startup sequence.",
            }
        ]
    return [
        {
            "LogDate": _iso(),
            "ProcessInfo": f"spid{rng.randint(50, 90)}",
            "Text": (
                "Error: 1205, Severity: 13, State: 1. Transaction "
                f"(Process ID {rng.randint(50, 90)}) was deadlocked on "
                "lock resources with another process and has been chosen "
                "as the deadlock victim."
            ),
        }
    ]


def _database_status_rows(rng: random.Random, quiet: bool) -> list[dict[str, Any]]:
    rows = [
        {"name": name, "state_desc": "ONLINE"}
        for name in ("master", "model", "msdb", "Sales", "Orders", "Archive")
    ]
    if not quiet:
        rows[rng.randint(0, len(rows) - 1)]["state_desc"] = rng.choice(
            ("OFFLINE", "SUSPECT")
        )
    return rows


def _storage_rows(rng: random.Random, quiet: bool) -> list[dict[str, Any]]:
    free_pct = rng.randint(30, 60) if quiet else rng.randint(1, 8)
    size = 10_000
    return [
        {
            "db_name": "Sales",
            "file_name": "Sales",
            "size_mb": size,
            "used_mb": int(size * (100 - free_pct) / 100),
        },
        {
            "db_name": "Orders",
            "file_name": "Orders",
            "size_mb": size,
            "used_mb": int(size * rng.randint(40, 70) / 100),
        },
    ]


def _refresh_job_rows(rng: random.Random, quiet: bool) -> list[dict[str, Any]]:
    if quiet:
        return []
    failures = rng.randint(1, 2)
    return [
        {
            "job_name": "DW Refresh",
            "step_name": rng.choice(("Load FactSales", "Merge Dimensions")),
            "step_id": rng.randint(1, 4),
            "subsystem": "SSIS",
            "run_status": 0,
            "message": "Error: The data flow task \"Load FactSales\" failed. "
            "The component reported the following error code: 0xC020801C.",
        }
        for _ in range(failures)
    ]


def _replication_rows(rng: random.Random, quiet: bool) -> list[dict[str, Any]]:
    latency = rng.randint(1, 4) if quiet else rng.randint(12, 75)
    return [
        {
            "publication_name": "SalesPub",
            "publisher_db": "Sales",
            "subscriber_db": "SalesReplica",
            "agent_name": "SalesPub-SalesReplica-1",
            "latency_minutes": latency,
        }
    ]


def _log_shipping_rows(rng: random.Random, quiet: bool) -> list[dict[str, Any]]:
    lag = rng.randint(2, 10) if quiet else rng.randint(20, 140)
    return [
        {
            "primary_server": "SQL-PRIMARY",
            "primary_database": "Reporting",
            "secondary_database": "Reporting_Standby",
            "last_restored_time": _iso(days_ago=lag / 1440.0),
            "restore_lag_minutes": lag,
        }
    ]


def _backup_rows(rng: random.Random, quiet: bool) -> list[dict[str, Any]]:
    stale_days = rng.randint(0, 3) if quiet else rng.randint(8, 30)
    return [
        {"database_name": name, "last_full": _iso(days_ago=stale_days),
         "last_diff": None, "last_log": None}
        for name in ("Sales", "Orders")
    ]


_ROW_BUILDERS: dict[str, Any] = {
    "blocking_now": _blocking_rows,
    "deadlock_history": _deadlock_rows,
    "top_queries": _top_query_rows,
    "cpu_schedulers": _cpu_rows,
    "failed_agent_jobs": _agent_job_rows,
    "sql_server_errors": _error_rows,
    "database_status": _database_status_rows,
    "storage_pressure": _storage_rows,
    "refresh_job_failures": _refresh_job_rows,
    "replication_latency": _replication_rows,
    "log_shipping_lag": _log_shipping_rows,
    "backup_freshness": _backup_rows,
}


def generate_synthetic_report(
    inv: Investigation,
    organization: str,
    seed: int | None = None,
) -> dict[str, Any]:
    """A full report payload, deterministic given (seed or the inv id).

    Schema-identical to the seeded demo report so every consumer —
    dashboard, PDF, tests — works unchanged. Evidence rows are fed
    through the real analysis engine, so the demo findings, primary
    cause, confidence, and guidance all come from the product's actual
    rules (#50) — over clearly-labeled sample data (#26).
    """
    from app.store import build_demo_report  # late import: no cycle

    effective_seed = inv.id if seed is None else seed
    rng = random.Random(effective_seed)
    report = build_demo_report(inv, organization)

    # Decide per area whether the sample data shows a problem (~80%)
    # or stays quiet — but always at least one area with signals, so a
    # demo report is never a blank analysis.
    areas = sorted({entry.issue_area for entry in inv.catalog})
    quiet_by_area = {area: rng.random() < 0.2 for area in areas}
    if all(quiet_by_area.values()):
        quiet_by_area[areas[0]] = False

    collection: list[dict[str, Any]] = []
    for entry in inv.catalog:
        builder = _ROW_BUILDERS.get(entry.id)
        rows = (
            builder(rng, quiet_by_area[entry.issue_area])
            if builder is not None
            else []
        )
        collection.append(
            {
                "source_id": entry.id,
                "title": f"{entry.title} (synthetic sample)",
                "category": entry.category,
                "issue_area": entry.issue_area,
                "status": "collected",
                "note": "sample rows",
                "evidence": [
                    {**row, "synthetic": True} for row in rows
                ],
                "row_count": len(rows),
            }
        )

    result = analysis.analyze(
        collection,
        investigation_time=report["investigation_time"],
        now=_SYNTHETIC_NOW,
    )
    report.update(result)

    # Make the sample nature impossible to miss on every signal.
    for area in report["findings_by_area"]:
        for finding in area["findings"]:
            finding["title"] += " (synthetic sample)"
            finding["description"] = (
                f"{finding['description']} [sample signal over generated "
                "data — a real server was not consulted]"
            )

    report["rationale"] = (
        f"{SYNTHETIC_LABEL} {report['rationale']} "
        f"Deterministic sample (seed {effective_seed})."
    )
    report["synthetic"] = True
    return report


def main(argv: list[str] | None = None) -> int:
    """Print a synthetic sample report as JSON (fixtures, frontend dev)."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--host", default="sql-synthetic.example")
    args = parser.parse_args(argv)

    from app.store import CATALOG, Investigation

    inv = Investigation(
        id=args.seed if args.seed is not None else 1,
        status="completed",
        server_host=args.host,
        server_port=1433,
        auth_type="sql",
        databases=None,
        lookback_hours=4,
        created_at="2026-01-01T00:00:00Z",
        catalog=list(CATALOG),
    )
    report = generate_synthetic_report(inv, "Synthetic Org", seed=args.seed)
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
