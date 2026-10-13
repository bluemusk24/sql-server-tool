/**
 * Centralized services layer — shared types.
 *
 * Every backend call the UI needs is expressed as a method on
 * `ServiceClient` (see serviceClient.ts). This module holds the
 * request/response types shared between the real (HTTP) and mock
 * implementations so the two can never drift.
 */

// ── Auth ────────────────────────────────────────────────────────────
export interface Session {
  organization: string;
  actor: string;
}

/** POST /auth/login request body. */
export interface LoginRequest {
  username: string;
  password: string;
}

/** POST /auth/login response — an opaque bearer token. */
export interface TokenResponse {
  access_token: string;
  token_type: string;
}

export interface AuthorizationStart {
  authorize_url: string;
}

// ── Health ──────────────────────────────────────────────────────────
export interface HealthStatus {
  status: string;
  version: string;
}

// ── Catalog ─────────────────────────────────────────────────────────
export interface CatalogEntry {
  id: string;
  key: string;
  title: string;
  description: string;
  purpose: string;
  category: string;
  issue_area: string;
  sql: string;
  sql_text: string;
  params: string[];
  requires_approval: boolean;
  expected_impact: string;
  versions: string[];
  min_permission: string;
  read_only: boolean;
}

// ── Investigations ─────────────────────────────────────────────────
export interface CreateInvestigationRequest {
  server_host: string;
  server_port?: number;
  auth_type: "sql" | "ad";
  databases?: string[] | null;
  lookback_hours: 1 | 4 | 8 | 24;
}

export interface InvestigationResponse {
  id: number;
  status: string;
  catalog: CatalogEntry[];
}

export interface InvestigationSummary {
  id: number;
  status: string;
}

// ── Approvals ──────────────────────────────────────────────────────
export type Decision = "approved" | "rejected";
export type ApprovalState = "pending" | "approved" | "rejected";

export interface ApprovalRequest {
  decisions: Record<string, Decision>;
  note?: string | null;
}

export interface ApprovalQuery {
  catalog_id: string;
  decision: Decision | null;
  actor: string | null;
  note: string | null;
  created_at: string | null;
}

export interface ApprovalResponse {
  investigation_id: number;
  state: ApprovalState;
  queries: ApprovalQuery[];
}

// ── Jobs ───────────────────────────────────────────────────────────
export type JobState = "queued" | "running" | "succeeded" | "failed" | "canceled";

export interface RunAccepted {
  job_id: string;
}

export interface JobStatusResponse {
  job_id: string;
  status: JobState;
  investigation_id: number | null;
  result: unknown;
  error: string | null;
}

// ── Report payload (dashboard / PDF) ────────────────────────────────
export type Severity = "high" | "medium" | "low";
export type AreaSeverity = Severity | "none";
export type ConfidenceLabel = "low" | "medium" | "high";
export type SourceStatus = "collected" | "partial" | "unavailable";

export interface EvidenceItem {
  evidence_id: string;
  area: string;
  timestamp: string | null;
  description: string;
  details: Record<string, unknown>;
}

export interface DataSourceReport {
  source_id: string;
  title: string;
  category: string;
  issue_area: string;
  status: SourceStatus;
  checked: boolean;
  note: string;
}

export interface CoverageGap {
  source_id: string;
  issue_area: string;
  status: SourceStatus;
  note: string;
}

export interface Signal {
  rule_id: string;
  area: string;
  severity: Severity;
  title: string;
  description: string;
  evidence_ids: string[];
}

export interface AreaFindings {
  area: string;
  severity: AreaSeverity;
  confidence_percent: number;
  confidence_label: ConfidenceLabel;
  signal_count: number;
  findings: Signal[];
  evidence: EvidenceItem[];
}

export interface RemediationStep {
  step: string;
  draft_sql: string | null;
  disclaimer: string;
}

export interface Recommendation {
  area: string | null;
  validation_checks: string[];
  remediation: RemediationStep[];
  disclaimer: string;
}

export interface Cause {
  title: string;
  description: string;
  confidence_percent: number;
  confidence_label: ConfidenceLabel;
}

export interface ReportPayload {
  investigation_id: number | null;
  organization: string;
  server_host: string;
  server_port: number;
  database_scope: string;
  databases: string[];
  lookback_hours: number;
  investigation_time: string;
  data_sources: DataSourceReport[];
  findings_by_area: AreaFindings[];
  primary_cause: Cause;
  alternatives: Cause[];
  confidence_percent: number;
  confidence_label: ConfidenceLabel;
  severity: Severity;
  rationale: string;
  inconclusive: boolean;
  coverage_gaps: CoverageGap[];
  recommendations: Recommendation[];
  validation_checks: string[];
  remediation: RemediationStep[];
  read_only_disclaimer: string;
}

// ── Orgs (#27) ─────────────────────────────────────────────────────
export interface Organization {
  id: number;
  name: string;
  issuer: string;
}

export interface CreateOrganizationRequest {
  name: string;
  issuer: string;
}
