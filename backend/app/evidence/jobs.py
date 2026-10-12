"""SQL Agent job-failure evidence collector (plan.md §4 area 3, #15).

Normalises the raw ``msdb.dbo.sysjobhistory``/``sysjobs`` rows returned by the
catalog source ``failed_agent_jobs`` into structured
:class:`~app.evidence.models.EvidenceRow` findings: one row per failed job run,
carrying the job name, the run outcome, the failure message when the server
recorded one, and a ``repeated`` flag for a job that failed more than once in
the #9 lookback window.

The catalog query already filters ``run_status = 0``, but the collector stays
defensive: a history row whose outcome is a success is skipped, so a success can
never become evidence. SQL Agent writes one history row per step plus a
job-outcome row (``step_id = 0``). When outcome rows are present each is treated
as one run and is enriched with the failed step messages recorded for that job,
so one failed run is reported once and ``repeated`` counts runs, not steps; a
single job failing at several steps is therefore not "repeated". When the source
carries no outcome row (a step-only history) each failed row is reported and
repeat detection falls back to the ``(job_name, run_date, run_time)`` run key.

The collector only reads rows the #12 engine already fetched; it never executes
SQL and never starts or stops a job (plan.md §6 - read-only, no remediation).
Job step output is redacted with the #5 filter before it reaches a finding.
Timestamps are reconstructed as timezone-aware UTC (SQL Agent history stores
naive server-local time and carries no offset); ordering comes from the
timestamp, never the row order.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any

from app.evidence.collectors import SourceEvidence, failure_status, value
from app.evidence.models import EvidenceRow, QueryStatus, SourceStatus
from app.evidence.registry import EvidenceRegistry
from app.logredact import redact_text

#: Catalog id of the failed-SQL-Agent-job history query (#8).
SOURCE_ID = "failed_agent_jobs"

#: Issue area every emitted row belongs to.
AREA = "agent_jobs"

#: ``run_status`` of a failed step or job outcome in ``msdb.dbo.sysjobhistory``.
FAILED_RUN_STATUS = 0

#: ``step_id`` of the job-outcome row that summarizes one whole job run.
JOB_OUTCOME_STEP_ID = 0

#: Human labels for the ``run_status`` codes SQL Agent records.
RUN_STATUS_LABELS: dict[int, str] = {
    0: "failed",
    1: "succeeded",
    2: "retry",
    3: "canceled",
    4: "in progress",
}

#: Longest message kept verbatim in a row's details; longer text is truncated.
MESSAGE_LIMIT = 500

#: Longest single-line message summary used in the description.
SUMMARY_LIMIT = 200


def _as_int(raw: Any) -> int | None:
    """Return ``raw`` as an ``int``, or ``None`` when it is not an integer.

    Booleans are rejected (``True`` is not a status code) and a non-integral
    float is treated as unavailable.
    """
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw) if raw.is_integer() else None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _run_status(row: Any) -> int | None:
    """The ``run_status`` code for ``row``, or ``None`` when unavailable."""
    return _as_int(value(row, "run_status"))


def _is_failed(row: Any) -> bool:
    """Whether ``row`` is a failed run.

    A missing ``run_status`` is treated as failed because the catalog source is
    already filtered to failures; an explicit success/retry/canceled code is
    never evidence.
    """
    code = _run_status(row)
    return code is None or code == FAILED_RUN_STATUS


def _outcome_label(code: int | None) -> str:
    """The human label for a ``run_status`` code (the code itself if unknown)."""
    if code is None:
        return "unknown"
    return RUN_STATUS_LABELS.get(code, str(code))


def _job_name(row: Any) -> str:
    """The job name for ``row``, or ``"unknown job"`` when unavailable."""
    raw = value(row, "job_name", None)
    if raw is None:
        return "unknown job"
    text = str(raw).strip()
    return text or "unknown job"


def _message(row: Any) -> str | None:
    """The redacted, stripped failure message for ``row``, or ``None``.

    The #5 filter is applied so a credential embedded in job step output never
    reaches a finding (the #15 constraint).
    """
    raw = value(row, "message", None)
    if raw is None:
        return None
    text = redact_text(str(raw)).strip()
    return text or None


def _parse_run_datetime(run_date: Any, run_time: Any) -> datetime | None:
    """Combine ``run_date`` (``YYYYMMDD``) and ``run_time`` (``HHMMSS``) as UTC.

    SQL Agent history stores naive server-local wall-clock values with no
    offset; the tool has no server timezone, so the value is taken as UTC rather
    than left naive (the #15 constraint requires timezone-aware timestamps).
    Returns ``None`` when either part is missing or unparseable.
    """
    date_code = _as_int(run_date)
    if date_code is None:
        return None
    time_code = _as_int(run_time)
    date_text = f"{date_code:08d}"
    time_text = f"{time_code:06d}" if time_code is not None else "000000"
    try:
        year = int(date_text[0:4])
        month = int(date_text[4:6])
        day = int(date_text[6:8])
        hour = int(time_text[0:2])
        minute = int(time_text[2:4])
        second = int(time_text[4:6])
        return datetime(year, month, day, hour, minute, second, tzinfo=UTC)
    except ValueError:
        return None


def _timestamp(row: Any) -> datetime | None:
    """The run timestamp for ``row``, or ``None`` when it cannot be parsed."""
    return _parse_run_datetime(value(row, "run_date"), value(row, "run_time"))


def _collapse(text: str, limit: int) -> str:
    """Collapse whitespace to one line and truncate to ``limit`` characters."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit].rstrip() + "..."


def _step_label(row: Any) -> str:
    """A ``step 2 'Load'`` style label for a failed step row, or an empty string."""
    step_id = _as_int(value(row, "step_id"))
    step_name = value(row, "step_name", None)
    name_text = str(step_name).strip() if step_name is not None else ""
    if step_id is None and not name_text:
        return ""
    if name_text:
        return f"step {step_id} '{name_text}'" if step_id is not None else name_text
    return f"step {step_id}"


def _group_runs(rows: list[Any]) -> list[list[Any]]:
    """Group failed history rows into runs, preserving first-seen order.

    Outcome rows (``step_id = 0``), when present, are each their own run. With
    no outcome row the rows are grouped by ``(job_name, run_date, run_time)`` so
    a step-only history still yields one group per run.
    """
    outcome_rows = [
        row for row in rows if _as_int(value(row, "step_id")) == JOB_OUTCOME_STEP_ID
    ]
    if outcome_rows:
        return [[row] for row in outcome_rows]

    groups: dict[tuple[Any, Any, Any], list[Any]] = {}
    order: list[tuple[Any, Any, Any]] = []
    for row in rows:
        key = (_job_name(row), value(row, "run_date"), value(row, "run_time"))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)
    return [groups[key] for key in order]


def _failed_steps_for(rows: list[Any], run: list[Any]) -> list[Any]:
    """Failed step rows for the same job and day as the outcome row of ``run``.

    Used only to surface the step message when an outcome row itself carries
    none; the date is matched (not the exact time) because a step row records
    when the step started and the outcome row when the job finished.
    """
    primary = run[0]
    if _as_int(value(primary, "step_id")) != JOB_OUTCOME_STEP_ID:
        return []
    job_name = _job_name(primary)
    run_date = value(primary, "run_date")
    steps = []
    for row in rows:
        if row is primary:
            continue
        step_id = _as_int(value(row, "step_id"))
        if step_id is None or step_id == JOB_OUTCOME_STEP_ID:
            continue
        if _job_name(row) != job_name or value(row, "run_date") != run_date:
            continue
        if _is_failed(row):
            steps.append(row)
    return steps


def _build_finding(run: list[Any], rows: list[Any], failed_runs: int) -> EvidenceRow:
    """Build one :class:`EvidenceRow` for a failed job run."""
    primary = run[0]
    job_name = _job_name(primary)
    code = _run_status(primary)
    repeated = failed_runs >= 2

    steps = _failed_steps_for(rows, run)
    message = _message(primary)
    step_label = _step_label(primary)
    if message is None:
        for step in steps:
            step_message = _message(step)
            if step_message:
                message = step_message
                step_label = _step_label(step)
                break

    timestamps = [t for t in (_timestamp(row) for row in run) if t is not None]
    timestamp = min(timestamps) if timestamps else None

    if message:
        summary = _collapse(message, SUMMARY_LIMIT)
        detail_text = f"{step_label}: {summary}" if step_label else summary
    else:
        detail_text = "no failure message was recorded"

    prefix = "Repeated SQL Agent job failure" if repeated else "SQL Agent job failure"
    when = f" at {timestamp.isoformat()}" if timestamp is not None else ""
    description = (
        f"{prefix}: job '{job_name}' "
        f"({_outcome_label(code)}){when}; {detail_text}."
    )

    return EvidenceRow(
        area=AREA,
        timestamp=timestamp,
        description=description,
        status=SourceStatus.collected,
        details={
            "job_name": job_name,
            "run_status": code,
            "run_outcome": _outcome_label(code),
            "message": _collapse(message, MESSAGE_LIMIT) if message else None,
            "repeated": repeated,
            "failed_runs": failed_runs,
            "step_id": _as_int(value(primary, "step_id")),
            "step_name": value(primary, "step_name", None),
            "run_date": value(primary, "run_date", None),
            "run_time": value(primary, "run_time", None),
            "failed_step_count": len(steps),
        },
    )


class AgentJobsCollector:
    """One row per failed job run; zero rows is still ``collected``."""

    source_id = SOURCE_ID

    def collect(
        self, evidence: SourceEvidence, registry: EvidenceRegistry
    ) -> list[EvidenceRow]:
        if evidence.status is not QueryStatus.ok:
            status, note = failure_status(evidence)
            registry.record(self.source_id, status, note)
            return []

        rows = [row for row in evidence.rows if _is_failed(row)]
        runs = _group_runs(rows)
        counts = Counter(_job_name(run[0]) for run in runs)
        findings = [_build_finding(run, rows, counts[_job_name(run[0])]) for run in runs]
        findings.sort(key=_sort_key)

        repeated_jobs = sum(1 for count in counts.values() if count >= 2)
        registry.record(
            self.source_id,
            SourceStatus.collected,
            f"{len(findings)} failed job run(s) across {len(counts)} job(s); "
            f"{repeated_jobs} repeated.",
        )
        return findings


def _sort_key(finding: EvidenceRow) -> tuple[bool, datetime]:
    """Order findings by timestamp ascending, undated findings last."""
    if finding.timestamp is None:
        return (True, datetime.min.replace(tzinfo=UTC))
    return (False, finding.timestamp)


def collect_agent_jobs(
    evidence: SourceEvidence, registry: EvidenceRegistry
) -> list[EvidenceRow]:
    """Run the SQL Agent job collector and return its structured rows."""
    return AgentJobsCollector().collect(evidence, registry)
