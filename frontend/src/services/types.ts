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

// ── Health ──────────────────────────────────────────────────────────
export interface HealthStatus {
  status: string;
  version: string;
  /** Served commit (#38); null outside Render. */
  commit?: string | null;
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

/** Lookback windows the investigation API accepts.
 *
 * Single source of truth for this choice list. Mirrors
 * `LookbackHours = Literal[1, 4, 8, 24]` in `backend/app/models.py` (#9) —
 * change both together.
 */
export const LOOKBACK_HOURS = [1, 4, 8, 24] as const;

export type LookbackHours = (typeof LOOKBACK_HOURS)[number];

export interface CreateInvestigationRequest {
  server_host: string;
  server_port?: number;
  auth_type: "sql" | "ad";
  databases?: string[] | null;
  lookback_hours: LookbackHours;
}

/**
 * The target-server fields returned by every investigation endpoint
 * (#34), so the UI can always show which SQL Server is under
 * investigation — list, approvals, and report alike.
 */
export interface InvestigationTarget {
  server_host: string;
  server_port: number;
  auth_type: "sql" | "ad";
  databases: string[] | null;
  lookback_hours: number;
  created_at: string;
}

export interface InvestigationResponse extends InvestigationTarget {
  id: number;
  status: string;
  catalog: CatalogEntry[];
}

export interface InvestigationSummary extends InvestigationTarget {
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

/** Per-investigation SQL credentials (#31).
 *
 * Session-only by spec (§6): held in the browser's memory (see
 * `credentials.ts`), sent with the run start, never persisted anywhere.
 */
export interface Credentials {
  username?: string | null;
  password: string;
}

/** Optional body on POST /investigations/{id}/run (#31). */
export interface StartRunRequest {
  credentials?: Credentials | null;
}

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
  /** The finding's kind (engine rule), e.g. refresh_job_failure. */
  kind?: string;
  /** The database the finding is about, when it is database-scoped. */
  database?: string | null;
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
  /** Labeled sample data from the synthetic pipeline (#26). */
  synthetic?: boolean;
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

// ── Fleet view (#49) ───────────────────────────────────────────────
export interface FleetArea {
  findings: number;
  severity: Severity;
}

export interface FleetServer {
  server_host: string;
  server_port: number;
  investigations: number;
  last_investigation: string | null;
  findings_by_severity: Record<Severity, number>;
  total_findings: number;
  refresh_findings: number;
  areas: Record<string, FleetArea>;
  latest_report_id: number | null;
  latest_primary_cause: string;
  latest_confidence_percent: number;
  inconclusive: boolean;
}

export interface FleetFailure {
  investigation_id: number;
  server_host: string;
  server_port: number;
  area: string;
  severity: Severity;
  database: string | null;
  title: string;
  time: string | null;
}

export interface FleetTotals {
  servers: number;
  investigations: number;
  findings: Record<Severity, number>;
  refresh_findings: number;
}

export interface FleetSummary {
  organization: string;
  generated_at: string;
  totals: FleetTotals;
  servers: FleetServer[];
  recent_failures: FleetFailure[];
}
