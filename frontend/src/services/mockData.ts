/**
 * Deterministic mock data for the services layer.
 *
 * Every response the mock returns is built from these fixtures so the
 * UI can be developed and tested without a live backend. The shapes
 * mirror the real backend schemas (see app/report/schemas.py and the
 * per-area catalog entries) but are synthesised — no SQL Server needed.
 */

import type {
  AreaFindings,
  CatalogEntry,
  Cause,
  DataSourceReport,
  EvidenceItem,
  Recommendation,
  ReportPayload,
  Signal,
} from "./types";

// ── Catalog ─────────────────────────────────────────────────────────

export const MOCK_CATALOG: CatalogEntry[] = [
  {
    id: "blocking_now",
    key: "blocking_now",
    title: "Current blocking chains",
    description: "Identify head blockers and their victims right now.",
    purpose: "Detect active blocking that is preventing queries from completing.",
    category: "blocking",
    issue_area: "blocking",
    sql: "SELECT blocking_session_id, wait_type, wait_time_ms\nFROM sys.dm_exec_requests\nWHERE blocking_session_id <> 0;",
    sql_text: "SELECT blocking_session_id, wait_type, wait_time_ms\nFROM sys.dm_exec_requests\nWHERE blocking_session_id <> 0;",
    params: [],
    requires_approval: true,
    expected_impact: "Read-only DMV query; negligible load.",
    versions: ["2016", "2017", "2019", "2022"],
    min_permission: "VIEW SERVER STATE",
    read_only: true,
  },
  {
    id: "deadlock_history",
    key: "deadlock_history",
    title: "Deadlock graph history",
    description: "Pull recent deadlock graphs from the system health session.",
    purpose: "Find deadlocks that occurred within the lookback window.",
    category: "blocking",
    issue_area: "deadlocks",
    sql: "SELECT xed.xml_data.value('(//deadlock)[1]', 'XML') AS deadlock_graph\nFROM sys.dm_xe_session_targets xet\nJOIN sys.dm_xe_sessions xe ON xe.address = xet.event_session_address\nWHERE xe.name = 'system_health';",
    sql_text: "SELECT xed.xml_data.value('(//deadlock)[1]', 'XML') AS deadlock_graph\nFROM sys.dm_xe_session_targets xet\nJOIN sys.dm_xe_sessions xe ON xe.address = xet.event_session_address\nWHERE xe.name = 'system_health';",
    params: [],
    requires_approval: true,
    expected_impact: "Read-only; parses ring buffer.",
    versions: ["2016", "2017", "2019", "2022"],
    min_permission: "VIEW SERVER STATE",
    read_only: true,
  },
  {
    id: "top_queries",
    key: "top_queries",
    title: "Top resource-consuming queries",
    description: "Rank queries by CPU, reads, writes, and duration.",
    purpose: "Surface the queries consuming the most resources.",
    category: "queries",
    issue_area: "queries",
    sql: "SELECT TOP 5 qs.execution_count,\n       qs.total_worker_time / 1000.0 AS total_cpu_ms,\n       qs.total_logical_reads,\n       st.text\nFROM sys.dm_exec_query_stats qs\nCROSS APPLY sys.dm_exec_sql_text(qs.sql_handle) st\nORDER BY qs.total_worker_time DESC;",
    sql_text: "SELECT TOP 5 qs.execution_count,\n       qs.total_worker_time / 1000.0 AS total_cpu_ms,\n       qs.total_logical_reads,\n       st.text\nFROM sys.dm_exec_query_stats qs\nCROSS APPLY sys.dm_exec_sql_text(qs.sql_handle) st\nORDER BY qs.total_worker_time DESC;",
    params: [],
    requires_approval: true,
    expected_impact: "Read-only; scans query plan cache.",
    versions: ["2016", "2017", "2019", "2022"],
    min_permission: "VIEW SERVER STATE",
    read_only: true,
  },
  {
    id: "cpu_schedulers",
    key: "cpu_schedulers",
    title: "CPU and scheduler pressure",
    description: "Check runnable tasks and scheduler waits per CPU.",
    purpose: "Detect CPU saturation at the SQL Server scheduler level.",
    category: "queries",
    issue_area: "cpu",
    sql: "SELECT scheduler_id,\n       current_tasks_count,\n       runnable_tasks_count,\n       current_workers_count\nFROM sys.dm_os_schedulers\nWHERE scheduler_id < 255;",
    sql_text: "SELECT scheduler_id,\n       current_tasks_count,\n       runnable_tasks_count,\n       current_workers_count\nFROM sys.dm_os_schedulers\nWHERE scheduler_id < 255;",
    params: [],
    requires_approval: true,
    expected_impact: "Read-only DMV query; negligible load.",
    versions: ["2016", "2017", "2019", "2022"],
    min_permission: "VIEW SERVER STATE",
    read_only: true,
  },
  {
    id: "failed_agent_jobs",
    key: "failed_agent_jobs",
    title: "Failed SQL Agent jobs",
    description: "List failed job runs from the lookback window.",
    purpose: "Flag every failed SQL Agent job and highlight repeats.",
    category: "agent_jobs",
    issue_area: "agent_jobs",
    sql: "SELECT j.name AS job_name,\n       h.run_status,\n       h.message\nFROM msdb.dbo.sysjobhistory h\nJOIN msdb.dbo.sysjobs j ON j.job_id = h.job_id\nWHERE h.run_status = 0;",
    sql_text: "SELECT j.name AS job_name,\n       h.run_status,\n       h.message\nFROM msdb.dbo.sysjobhistory h\nJOIN msdb.dbo.sysjobs j ON j.job_id = h.job_id\nWHERE h.run_status = 0;",
    params: [],
    requires_approval: true,
    expected_impact: "Read-only; scans msdb job history.",
    versions: ["2016", "2017", "2019", "2022"],
    min_permission: "SELECT on msdb",
    read_only: true,
  },
  {
    id: "sql_server_errors",
    key: "sql_server_errors",
    title: "SQL Server error log",
    description: "Scan the current error log for error-level entries.",
    purpose: "Flag SQL Server errors within the lookback window.",
    category: "agent_jobs",
    issue_area: "errors",
    sql: "EXEC sp_readerrorlog 0, 1, 'error';",
    sql_text: "EXEC sp_readerrorlog 0, 1, 'error';",
    params: [],
    requires_approval: true,
    expected_impact: "Read-only; reads the SQL Server error log.",
    versions: ["2016", "2017", "2019", "2022"],
    min_permission: "VIEW SERVER STATE",
    read_only: true,
  },
  {
    id: "database_status",
    key: "database_status",
    title: "Database availability status",
    description: "Check each database's state (online, offline, suspect).",
    purpose: "Detect databases that are unavailable or in a suspect state.",
    category: "availability",
    issue_area: "availability",
    sql: "SELECT name, state_desc\nFROM sys.databases;",
    sql_text: "SELECT name, state_desc\nFROM sys.databases;",
    params: [],
    requires_approval: true,
    expected_impact: "Read-only DMV query; negligible load.",
    versions: ["2016", "2017", "2019", "2022"],
    min_permission: "VIEW ANY DATABASE",
    read_only: true,
  },
  {
    id: "storage_pressure",
    key: "storage_pressure",
    title: "Storage free-space percentage",
    description: "Report free-space percentage for each database file.",
    purpose: "Flag databases with less than 10% free space.",
    category: "availability",
    issue_area: "storage",
    sql: "SELECT DB_NAME(database_id) AS db_name,\n       name AS file_name,\n       size * 8 / 1024 AS size_mb,\n       FILEPROPERTY(name, 'SpaceUsed') * 8 / 1024 AS used_mb\nFROM sys.master_files;",
    sql_text: "SELECT DB_NAME(database_id) AS db_name,\n       name AS file_name,\n       size * 8 / 1024 AS size_mb,\n       FILEPROPERTY(name, 'SpaceUsed') * 8 / 1024 AS used_mb\nFROM sys.master_files;",
    params: [],
    requires_approval: true,
    expected_impact: "Read-only DMV query; negligible load.",
    versions: ["2016", "2017", "2019", "2022"],
    min_permission: "VIEW SERVER STATE",
    read_only: true,
  },
];

// ── Report payload ──────────────────────────────────────────────────

const SOURCES: DataSourceReport[] = [
  { source_id: "blocking_now", title: "Current blocking chains", category: "blocking", issue_area: "blocking", status: "collected", checked: true, note: "" },
  { source_id: "deadlock_history", title: "Deadlock graph history", category: "blocking", issue_area: "deadlocks", status: "collected", checked: true, note: "" },
  { source_id: "top_queries", title: "Top resource-consuming queries", category: "queries", issue_area: "queries", status: "collected", checked: true, note: "" },
  { source_id: "cpu_schedulers", title: "CPU and scheduler pressure", category: "queries", issue_area: "cpu", status: "partial", checked: true, note: "scheduler 4 data unavailable" },
  { source_id: "failed_agent_jobs", title: "Failed SQL Agent jobs", category: "agent_jobs", issue_area: "agent_jobs", status: "collected", checked: true, note: "" },
  { source_id: "sql_server_errors", title: "SQL Server error log", category: "agent_jobs", issue_area: "errors", status: "collected", checked: true, note: "" },
  { source_id: "database_status", title: "Database availability status", category: "availability", issue_area: "availability", status: "collected", checked: true, note: "" },
  { source_id: "storage_pressure", title: "Storage free-space percentage", category: "availability", issue_area: "storage", status: "unavailable", checked: false, note: "master_files read denied" },
];

const SIGNALS: Signal[] = [
  {
    rule_id: "cpu_saturation",
    area: "cpu",
    severity: "high",
    title: "CPU saturation on scheduler 2",
    description: "Runnable tasks exceeded 10 for 5+ minutes.",
    evidence_ids: ["cpu_schedulers"],
  },
  {
    rule_id: "blocking_chain",
    area: "blocking",
    severity: "high",
    title: "Blocking chain from session 57",
    description: "Session 57 blocking 12+ victim sessions for >30 seconds.",
    evidence_ids: ["blocking_now"],
  },
  {
    rule_id: "slow_query_top",
    area: "queries",
    severity: "medium",
    title: "Top query consuming 45% of CPU",
    description: "A single ad-hoc query consumed the most CPU in the window.",
    evidence_ids: ["top_queries"],
  },
  {
    rule_id: "failed_backup_job",
    area: "agent_jobs",
    severity: "medium",
    title: "SQL Agent job 'NightlyBackup' failed twice",
    description: "Job failed with exit code 1 on consecutive runs.",
    evidence_ids: ["failed_agent_jobs"],
  },
];

const EVIDENCE: EvidenceItem[] = [
  { evidence_id: "cpu_schedulers", area: "cpu", timestamp: "2026-10-12T14:30:00Z", description: "Scheduler 2: runnable_tasks=14, current_tasks=42", details: { scheduler_id: 2, runnable_tasks: 14 } },
  { evidence_id: "blocking_now", area: "blocking", timestamp: "2026-10-12T14:28:00Z", description: "Head blocker session 57, wait type LCK_M_X, wait_time 45000ms", details: { blocking_session_id: 57 } },
  { evidence_id: "top_queries", area: "queries", timestamp: "2026-10-12T14:25:00Z", description: "Top query: 12 840 ms CPU, 1 240 000 logical reads", details: { cpu_ms: 12840, reads: 1240000 } },
  { evidence_id: "failed_agent_jobs", area: "agent_jobs", timestamp: "2026-10-12T13:00:00Z", description: "Job 'NightlyBackup' failed — exit code 1", details: { job_name: "NightlyBackup", run_status: 0 } },
];

const FINDINGS_BY_AREA: AreaFindings[] = [
  { area: "cpu", severity: "high", confidence_percent: 85, confidence_label: "high", signal_count: 1, findings: [SIGNALS[0]], evidence: [EVIDENCE[0]] },
  { area: "blocking", severity: "high", confidence_percent: 90, confidence_label: "high", signal_count: 1, findings: [SIGNALS[1]], evidence: [EVIDENCE[1]] },
  { area: "queries", severity: "medium", confidence_percent: 70, confidence_label: "medium", signal_count: 1, findings: [SIGNALS[2]], evidence: [EVIDENCE[2]] },
  { area: "agent_jobs", severity: "medium", confidence_percent: 65, confidence_label: "medium", signal_count: 1, findings: [SIGNALS[3]], evidence: [EVIDENCE[3]] },
  { area: "deadlocks", severity: "none", confidence_percent: 0, confidence_label: "low", signal_count: 0, findings: [], evidence: [] },
  { area: "errors", severity: "none", confidence_percent: 0, confidence_label: "low", signal_count: 0, findings: [], evidence: [] },
  { area: "availability", severity: "none", confidence_percent: 0, confidence_label: "low", signal_count: 0, findings: [], evidence: [] },
  { area: "storage", severity: "low", confidence_percent: 0, confidence_label: "low", signal_count: 0, findings: [], evidence: [] },
];

const PRIMARY_CAUSE: Cause = {
  title: "CPU saturation caused by ad-hoc query storm",
  description:
    "A single ad-hoc query consumed 45% of total CPU and generated a blocking chain. The scheduler runnable-task count confirms sustained pressure.",
  confidence_percent: 85,
  confidence_label: "high",
};

const ALTERNATIVES: Cause[] = [
  {
    title: "Failed backup job causing resource contention",
    description: "The NightlyBackup job failure may have triggered a retry storm.",
    confidence_percent: 35,
    confidence_label: "low",
  },
  {
    title: "Storage pressure leading to I/O waits",
    description: "Storage data was unavailable; cannot rule this out.",
    confidence_percent: 15,
    confidence_label: "low",
  },
];

const RECOMMENDATIONS: Recommendation[] = [
  {
    area: "cpu",
    validation_checks: [
      "Review sys.dm_exec_requests for currently high-CPU queries",
      "Confirm scheduler runnable-task count has returned to baseline",
    ],
    remediation: [
      { step: "Identify the top CPU-consuming query and review its plan.", draft_sql: "SELECT TOP 5 st.text, qs.total_worker_time\nFROM sys.dm_exec_query_stats qs\nCROSS APPLY sys.dm_exec_sql_text(qs.sql_handle) st\nORDER BY qs.total_worker_time DESC;", disclaimer: "recommendation only, execute at the DBA's discretion" },
      { step: "Consider adding a query store plan guide if the query is recurring.", draft_sql: null, disclaimer: "recommendation only, execute at the DBA's discretion" },
    ],
    disclaimer: "recommendation only, execute at the DBA's discretion",
  },
  {
    area: "blocking",
    validation_checks: ["Confirm the blocking chain has resolved", "Check for orphaned transactions"],
    remediation: [
      { step: "If blocking persists, identify the head blocker and its open transaction.", draft_sql: "SELECT session_id, open_transaction_count\nFROM sys.dm_exec_sessions\nWHERE session_id = 57;", disclaimer: "recommendation only, execute at the DBA's discretion" },
    ],
    disclaimer: "recommendation only, execute at the DBA's discretion",
  },
];

export const MOCK_REPORT: ReportPayload = {
  investigation_id: 42,
  organization: "Acme Corp",
  server_host: "sql01.acme.example",
  server_port: 1433,
  database_scope: "instance",
  databases: [],
  lookback_hours: 4,
  investigation_time: "2026-10-12T14:30:00Z",
  data_sources: SOURCES,
  findings_by_area: FINDINGS_BY_AREA,
  primary_cause: PRIMARY_CAUSE,
  alternatives: ALTERNATIVES,
  confidence_percent: 85,
  confidence_label: "high",
  severity: "high",
  rationale:
    "Sustained CPU saturation on scheduler 2, a top ad-hoc query, and a blocking chain from a long-running transaction point to a query-driven CPU incident.",
  inconclusive: false,
  coverage_gaps: [
    { source_id: "storage_pressure", issue_area: "storage", status: "unavailable", note: "master_files read denied" },
  ],
  recommendations: RECOMMENDATIONS,
  validation_checks: RECOMMENDATIONS.flatMap((r) => r.validation_checks),
  remediation: RECOMMENDATIONS.flatMap((r) => r.remediation),
  read_only_disclaimer: "recommendation only, execute at the DBA's discretion",
};
