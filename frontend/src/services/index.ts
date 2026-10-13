/**
 * Barrel export + service-client factory.
 *
 * Import `getServiceClient` (or the named `services` singleton) from
 * anywhere in the app. The factory reads `VITE_USE_MOCK` once and
 * returns either the mock or the real HTTP client — components never
 * need to know which.
 */

import type { ServiceClient } from "./serviceClient";
import { realServiceClient } from "./realService";
import { mockServiceClient } from "./mockService";

// ── Types (re-exported so consumers import from one place) ──────────
export type {
  ApprovalRequest,
  ApprovalResponse,
  AuthorizationStart,
  AreaFindings,
  AreaSeverity,
  CatalogEntry,
  Cause,
  ConfidenceLabel,
  CoverageGap,
  CreateInvestigationRequest,
  CreateOrganizationRequest,
  DataSourceReport,
  Decision,
  EvidenceItem,
  HealthStatus,
  InvestigationResponse,
  InvestigationSummary,
  JobState,
  JobStatusResponse,
  LookbackHours,
  LoginRequest,
  Organization,
  Recommendation,
  RemediationStep,
  ReportPayload,
  RunAccepted,
  Session,
  Severity,
  Signal,
  SourceStatus,
  TokenResponse,
} from "./types";

export { ApiError } from "./http";
export { LOOKBACK_HOURS } from "./types";

// ── Factory ─────────────────────────────────────────────────────────

function resolveMock(): boolean {
  const flag = import.meta.env.VITE_USE_MOCK;
  if (typeof flag === "string") return flag !== "false" && flag !== "0";
  return true; // default: mock on
}

const services: ServiceClient = resolveMock() ? mockServiceClient : realServiceClient;

export function getServiceClient(): ServiceClient {
  return services;
}

// Convenience: a named singleton for the common case
export { services };
