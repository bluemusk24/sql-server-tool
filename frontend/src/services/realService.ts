/**
 * Real HTTP implementation of ServiceClient.
 *
 * Delegates to the centralised http.ts transport. Components use this
 * when `VITE_USE_MOCK=false`.
 */

import { request } from "./http";
import { setToken, clearToken } from "./token";
import type {
  ApprovalResponse,
  FleetSummary,
  HealthStatus,
  InvestigationResponse,
  InvestigationSummary,
  JobStatusResponse,
  LoginRequest,
  Organization,
  ReportPayload,
  RunAccepted,
  Session,
  TokenResponse,
} from "./types";
import type { ServiceClient } from "./serviceClient";

export const realServiceClient: ServiceClient = {
  // Auth
  login: async (credentials: LoginRequest) => {
    const token = await request<TokenResponse>("/auth/login", {
      method: "POST",
      body: credentials,
    });
    setToken(token.access_token);
    return request<Session>("/auth/session");
  },
  logout: () => clearToken(),
  getSession: () => request<Session>("/auth/session"),

  // Health
  getHealth: () => request<HealthStatus>("/health"),

  // Investigations
  listInvestigations: () => request<InvestigationSummary[]>("/investigations"),
  getInvestigation: (id) => request<InvestigationResponse>(`/investigations/${id}`),
  createInvestigation: (payload) =>
    request<InvestigationResponse>("/investigations", { method: "POST", body: payload }),
  deleteInvestigation: (id) =>
    request<void>(`/investigations/${id}`, { method: "DELETE" }),

  // Approvals
  getApprovals: (id) => request<ApprovalResponse>(`/investigations/${id}/approvals`),
  recordDecisions: (id, payload) =>
    request<ApprovalResponse>(`/investigations/${id}/approvals`, { method: "POST", body: payload }),

  // Jobs
  startRun: (id, credentials) =>
    request<RunAccepted>(`/investigations/${id}/run`, {
      method: "POST",
      body: credentials ? { credentials } : undefined,
    }),
  getJobStatus: (jobId) => request<JobStatusResponse>(`/jobs/${encodeURIComponent(jobId)}`),
  cancelJob: (jobId) => request<JobStatusResponse>(`/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" }),

  // Reports
  getReportPayload: (id) => request<ReportPayload>(`/reports/${id}`),
  downloadReportPdf: (id) => request<Blob>(`/reports/${id}.pdf`, { binary: true }),

  // Fleet (#49)
  getFleet: () => request<FleetSummary>("/fleet"),

  // Orgs
  listOrganizations: () => request<Organization[]>("/orgs"),
  createOrganization: (payload) =>
    request<Organization>("/orgs", { method: "POST", body: payload }),
  deprovisionOrganization: (id) =>
    request<void>(`/orgs/${id}`, { method: "DELETE" }),
};
