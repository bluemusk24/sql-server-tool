"""Deterministic synthetic report generator (#26).

Produces realistic-shaped evidence and findings for every issue area so
the pipeline, the report dashboard, and the PDF can be exercised end to
end while real-data approval is pending. Everything it emits is
unmistakably labeled as synthetic — sample data, never evidence (spec
§3.8: nothing unverified is ever presented as real).

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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.store import Investigation

#: Carried on every generated payload, and repeated in the rationale in
#: plain words — nobody can mistake sample data for collected evidence.
SYNTHETIC_LABEL = (
    "SYNTHETIC SAMPLE DATA — not collected from any real server."
)

_SEVERITY_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}
_CONFIDENCE_LABELS = ((0, "low"), (50, "medium"), (75, "high"))

_SIGNAL_TITLES = (
    "Wait activity concentrated on a small session set",
    "Blocking chain observed within the lookback window",
    "Autogrowth events correlating with latency spikes",
    "Tempdb contention pattern present",
    "Long-running queries holding locks beyond the window",
)


def _confidence_label(percent: int) -> str:
    label = "low"
    for floor, name in _CONFIDENCE_LABELS:
        if percent >= floor:
            label = name
    return label


def _synthetic_signal(
    rng: random.Random, area: str, title: str, severity: str, evidence_ids: list[str]
) -> dict[str, Any]:
    return {
        "rule_id": f"synthetic-{rng.randrange(10**6):06d}",
        "area": area,
        "severity": severity,
        "title": f"{title} (synthetic sample)",
        "description": (
            f"{rng.choice(_SIGNAL_TITLES)} in area '{area}'. Sample signal "
            "over generated data — a validated rule would fire here if the "
            "pattern were real."
        ),
        "evidence_ids": list(evidence_ids),
    }


def generate_synthetic_report(
    inv: Investigation,
    organization: str,
    seed: int | None = None,
) -> dict[str, Any]:
    """A full report payload, deterministic given (seed or the inv id).

    Schema-identical to the seeded demo report so every consumer —
    dashboard, PDF, tests — works unchanged; unlike the demo shell, the
    findings are populated with clearly-labeled sample data (#26).
    """
    from app.store import build_demo_report  # late import: no cycle

    rng = random.Random(inv.id if seed is None else seed)
    report = build_demo_report(inv, organization)

    by_area: dict[str, list[str]] = {}
    for entry in inv.catalog:
        by_area.setdefault(entry.issue_area, []).append(entry.title)

    areas: list[dict[str, Any]] = []
    for area, titles in sorted(by_area.items()):
        quiet = rng.random() < 0.2
        area_severity = "none" if quiet else rng.choice(("low", "medium", "high"))
        evidence = [
            {
                "evidence_id": f"synthetic-{area}-{index}",
                "area": area,
                "timestamp": report["investigation_time"],
                "description": f"{title} (synthetic sample row)",
                "details": {
                    "sample_value": rng.randint(1, 999),
                    "synthetic": True,
                },
            }
            for index, title in enumerate(titles)
            for _ in range(rng.randint(1, 2))
        ]
        findings = [] if quiet else [
            _synthetic_signal(
                rng,
                area,
                rng.choice(titles),
                area_severity,
                [item["evidence_id"] for item in evidence],
            )
            for _ in range(rng.randint(1, min(2, len(titles)) or 1))
        ]
        confidence = rng.randint(40, 90)
        areas.append(
            {
                "area": area,
                "severity": area_severity,
                "confidence_percent": confidence,
                "confidence_label": _confidence_label(confidence),
                "signal_count": len(findings),
                "findings": findings,
                "evidence": evidence,
            }
        )
    report["findings_by_area"] = areas

    # The loudest area becomes the (synthetic) primary cause; ties break
    # on area name, keeping the whole report deterministic.
    ranked = sorted(
        (area for area in areas if area["severity"] != "none"),
        key=lambda area: (-_SEVERITY_RANK[area["severity"]], area["area"]),
    )

    def _cause(area: dict[str, Any]) -> dict[str, Any]:
        return {
            "title": f"Synthetic: {area['area']} pressure",
            "description": (
                f"Sample conclusion over generated {area['area']} data — "
                "illustrates how a validated decision rule would read the "
                "evidence. Not a finding about any real server."
            ),
            "confidence_percent": area["confidence_percent"],
            "confidence_label": area["confidence_label"],
        }

    if ranked:
        primary = ranked[0]
        report["primary_cause"] = _cause(primary)
        report["alternatives"] = [_cause(area) for area in ranked[1:4]]
        report["confidence_percent"] = primary["confidence_percent"]
        report["confidence_label"] = primary["confidence_label"]
        report["severity"] = primary["severity"]
    else:
        report["alternatives"] = []
        report["inconclusive"] = True

    disclaimer = report["read_only_disclaimer"]
    report["recommendations"] = [
        {
            "area": area["area"],
            "validation_checks": [
                (
                    f"Re-run the {area['area']} sources and compare the synthetic "
                    "pattern against the real signals once collection is approved."
                ),
            ],
            "remediation": [
                {
                    "step": (
                        f"(synthetic) Review the sessions behind the "
                        f"{area['area']} signals"
                    ),
                    "draft_sql": None,
                    "disclaimer": disclaimer,
                }
            ],
            "disclaimer": disclaimer,
        }
        for area in ranked
    ]
    report["validation_checks"] = [
        check
        for recommendation in report["recommendations"]
        for check in recommendation["validation_checks"]
    ]
    report["remediation"] = [
        step
        for recommendation in report["recommendations"]
        for step in recommendation["remediation"]
    ]
    report["inconclusive"] = False
    report["rationale"] = (
        f"{SYNTHETIC_LABEL} Deterministic sample (seed "
        f"{inv.id if seed is None else seed}): {len(ranked)} area(s) with "
        f"signals across {len(by_area)} issue area(s)."
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
