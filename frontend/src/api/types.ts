/**
 * Typed API models (#22).
 *
 * These mirror the backend response schemas so the client
 * (#9/#10/#11/#19 endpoints) is type-checked against the same
 * shapes the API returns. Field names match the Pydantic models
 * (snake_case), which is what the API serialises.
 */

/** GET /health (#2). */
export interface HealthStatus {
  status: string;
  version: string;
}

/** GET /auth/session — the session indicator (#30 provides it). */
export interface Session {
  organization: string;
  actor: string;
}

/** GET /auth/authorize — the OIDC authorize URL (#30 provides it). */
export interface AuthorizationStart {
  authorize_url: string;
}

/** One catalog entry (GET /catalog, #8), echoed verbatim. */
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

/** POST /investigations request (#9). */
export interface CreateInvestigationRequest {
  server_host: string;
  server_port?: number;
  auth_type: "sql" | "ad";
  databases?: string[] | null;
  lookback_hours: 1 | 4 | 8 | 24;
}

/** POST /investigations response (#9). */
export interface InvestigationResponse {
  id: number;
  status: string;
  catalog: CatalogEntry[];
}

/** A DBA decision on one catalog query (#10). */
export type Decision = "approved" | "rejected";

/** POST /investigations/{id}/approvals request (#10). */
export interface ApprovalRequest {
  decisions: Record<string, Decision>;
  note?: string | null;
}

/** The computed approval state of an investigation (#10). */
export type ApprovalState = "pending" | "approved" | "rejected";

/** The latest decision on one catalog query, or null (#10). */
export interface ApprovalQuery {
  catalog_id: string;
  decision: Decision | null;
  actor: string | null;
  note: string | null;
  created_at: string | null;
}

/** POST /investigations/{id}/approvals response (#10). */
export interface ApprovalResponse {
  investigation_id: number;
  state: ApprovalState;
  queries: ApprovalQuery[];
}

/** POST /investigations/{id}/run response (#11). */
export interface RunAccepted {
  job_id: string;
}

/** The job statuses the API exposes (#11). */
export type JobState = "queued" | "running" | "succeeded" | "failed";

/** GET /jobs/{job_id} response (#11). */
export interface JobStatusResponse {
  job_id: string;
  status: JobState;
  investigation_id: number | null;
  result: unknown;
  error: string | null;
}
