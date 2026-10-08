# SQL Server Diagnostic Assistant — POC Scope

**Status:** Scope draft, updated 2026-10-08

## 1. Objective

Build an on-demand diagnostic assistant that reduces the time DBAs spend initially diagnosing SQL Server and application performance issues. A DBA submits a SQL Server connection, chooses a recent-history window, reviews and approves the diagnostic query batch, and receives evidence, likely causes, validation checks, and remediation guidance.

## 2. POC boundaries

- **Database platform:** Microsoft SQL Server only.
- **Candidate environments:** Production and non-production, across multiple organizations.
- **Deployment types:** On-premises servers, virtual machines, and Azure SQL Managed Instance.
- **Deferred platform:** Azure SQL Database, which needs a separate per-database diagnostic path.
- **SQL Server versions and availability topologies:** Support the versions and Always On availability groups/failover clusters found during candidate selection.
- **Candidate count:** Set after DBAs identify candidate instances.
- **Candidate selection:** DBAs select a representative mix of environments/versions and instances with recurring incidents.
- **Use mode:** User-initiated investigations only. No scheduled scans, monitoring-tool integrations, ticket creation, or alert-triggered runs.
- **Instance input:** The DBA enters the server name and connection details for each investigation. No host allowlist; allow any reachable SQL Server subject to valid credentials.
- **Investigation scope:** Whole instance by default, with an option to select specific databases.
- **Lookback:** DBA selects a relative window up to 24 hours; suggested choices are 1, 4, 8, or 24 hours.
- **Issue coverage:** Every investigation checks all four areas, without prioritization:
  1. Blocking and deadlocks
  2. Slow queries and high CPU
  3. Failed SQL Agent jobs and SQL Server errors
  4. Database availability and storage pressure
- **Data scope:** SQL Server data only. No Windows host logs or performance counters. Skip Query Store.

## 3. Investigation workflow

1. The DBA signs in through their organization’s single sign-on.
2. The DBA enters the SQL Server connection details and supplies either Windows/Active Directory or SQL Server credentials for this investigation.
3. The DBA chooses whole-instance or database-scoped coverage and selects a lookback window.
4. The tool displays the complete diagnostic query list, including SQL text, purpose, and expected impact.
5. The DBA reviews and approves the queries together for this investigation. The run does not start unless all required queries are approved.
6. The tool runs approved read-only diagnostics and gathers available SQL Server evidence.
7. Built-in rules identify signals; AI correlates the evidence and produces a dashboard and incident report.
8. If an approved query or evidence source is unavailable because of permissions or server settings, continue with partial findings and identify the missing evidence. Do not claim that unavailable historical evidence was checked.

## 4. Diagnostic evidence and initial rule behavior

Collect available SQL Server context, including error log entries and diagnostic events, SQL Agent job history, blocking/deadlocks, query activity, waits, database status, storage indicators, and recent changes available through SQL Server data.

Initial behavior agreed for the POC:

- **Blocking:** Flag any blocking event detected. On-demand runs can inspect current blocking; historical blocking is available only when SQL Server has retained evidence. Clearly flag gaps.
- **Deadlocks:** Flag every deadlock found in the selected window, where retained evidence is available.
- **Slow/resource-intensive queries:** Report the top 5 resource-consuming queries per available resource category, regardless of runtime.
- **High CPU:** Use SQL Server CPU and scheduler signals; host counters are out of scope.
- **SQL Agent:** Flag every failed job in the selected window; highlight repeated or critical failures.
- **SQL Server errors:** Flag all errors found in the selected window.
- **Database availability:** Flag any database that is offline, suspect, or otherwise unavailable.
- **Storage pressure:** Start with less than 10% free space and use other available SQL Server indicators; finalize exact indicators during implementation.
- **Extended Events:** Per candidate instance, determine whether existing Extended Events data is available and approved to read. Do not create or change sessions.
- **Thresholds:** Use the same built-in thresholds across instances; DBAs will not tune thresholds during the POC. Use fixed thresholds and trends within the current investigation window only. Do not use metrics from earlier investigations to build baselines.

## 5. Analysis and DBA guidance

- Start with built-in diagnostic rules; no approved runbooks or known-issue records are available initially.
- AI correlates rule signals and SQL Server evidence to summarize the likely cause. Raw SQL text and server/database names may be used only within the organization-approved environment.
- Show one primary probable cause plus ranked alternatives.
- Show confidence as both a percentage and low/medium/high.
- Mark a result **inconclusive** when confidence is below 50% or evidence quality is insufficient; still show possible causes as low-confidence alternatives.
- Use high/medium/low severity labels.
- Include supporting evidence, recommended validation checks, and remediation guidance. Provide both step-by-step actions and draft SQL commands for DBA review.
- The tool reports and recommends only. It never executes remediation or makes production changes.
- Do not compare investigations with previous cases. Historical incident records may be used for offline POC evaluation only.

## 6. Access, deployment, and data handling

- Production collection is read-only. Every diagnostic query must be presented and approved by a DBA for each investigation.
- Support both Windows/Active Directory and SQL Server authentication for SQL Server connections.
- The DBA supplies SQL credentials per investigation. Treat credentials as session-only; never store them in history, reports, or application logs.
- Deploy in an organization-approved cloud environment with a secure network path to candidate SQL Servers. A Render-like provider is an option only if approved for production credentials and diagnostic data.
- Require each participating organization’s single sign-on for tool access. Within each organization, access is for DBAs only.
- Platform operators must have no cross-organization access to diagnostic contents.
- No shared repository currently exists. The POC needs a new shared SQL Server repository host, with a separate database for each organization.
- Each organization’s diagnostic data may be stored in the shared repository only after that organization approves it. Until then, use synthetic data.
- Keep an audit trail of investigations, query approvals, and report access; retain audit records for 7 days.
- Decide SQL connection encryption and certificate validation during security review.

## 7. Repository and retention

- Retain full evidence, including query text and raw diagnostic results, plus summaries and findings for 7 days.
- Credentials are excluded from stored data.
- Do not compare new findings with previous investigation records; history is retained for records and approved evaluation use only.

## 8. Dashboard and incident report

Show summaries and raw diagnostic results, with evidence accessible from the report. Include at least:

- Organization, server/instance, database scope, and selected lookback window
- Investigation time and data sources checked
- Findings by issue area, severity, confidence, and evidence
- Primary probable cause and ranked alternatives, including an inconclusive status where appropriate
- Missing/unavailable evidence and resulting coverage gaps
- Recommended validation checks and remediation guidance, including draft SQL for DBA review

Allow DBAs to download the incident report as PDF.

## 9. POC evaluation

Target reduction in initial diagnosis time from approximately 60 minutes to 10–15 minutes for supported incident types. Track mean time to diagnose, DBA investigation hours saved, incident classification accuracy, and repeat-incident resolution time.

- **Diagnosis accuracy:** Target at least 80% DBA-validated primary-cause accuracy before expansion; reassess after the first evaluation set.
- **Evaluation set:** Target 40 resolved cases, approximately 10 per issue area. If fewer are available, use the available cases and report sample size and gaps.
- **Validation:** The DBA who investigated each incident validates the historical root cause and each POC diagnosis.
- **Accuracy methods:** Use both DBA validation and comparison against resolved incident records.
- **Diagnosis time:** Use both DBA-recorded investigation time and comparison with similar past incidents.

## 10. Decisions and work remaining

### Awaiting security/organizational decisions

- Whether all participating organizations approve real diagnostic data in one shared repository host. Until approval, use synthetic data.
- Cloud host approval, network paths, and SQL connection encryption/certificate requirements.
- Exact candidate organizations, instances, SQL Server versions, topologies, permissions, and final instance count.

### To define during implementation

- Exact read-only diagnostic query catalog and minimum permissions by SQL Server version/deployment.
- Exact storage-pressure indicators and thresholds beyond the initial 10% free-space flag.
- SQL CPU/scheduler rules and the final interpretation of expected query impact.
- Repository schema, per-organization database provisioning, audit schema, and retention cleanup.
- How confidence percentages are calibrated and how the 80% accuracy gate is computed.
- PDF report layout and dashboard details.
