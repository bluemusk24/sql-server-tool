/**
 * Real HTTP implementation of ServiceClient.
 *
 * Delegates to the centralised http.ts transport. Components use this
 * when `VITE_USE_MOCK=false`.
 */

import { request } from "./http";
import type {
  ApprovalResponse,
  AuthorizationStart,
  HealthStatus,
  InvestigationResponse,
  InvestigationSummary,
  JobStatusResponse,
  Organization,
  ReportPayload,
  RunAccepted,
  Session,
} from "./types";
import type { ServiceClient } from "./serviceClient";

export const realServiceClient: ServiceClient = {
  // Auth
  getSession: () => request<Session>("/auth/session"),
  startAuthorization: () => request<AuthorizationStart>("/auth/authorize"),

  // Health
  getHealth: () => request<HealthStatus>("/health"),

  // Investigations
  listInvestigations: () => request<InvestigationSummary[]>("/investigations"),
  getInvestigation: (id) => request<InvestigationResponse>(`/investigations/${id}`),
  createInvestigation: (payload) =>
    request<InvestigationResponse>("/investigations", { method: "POST", body: payload }),

  // Approvals
  getApprovals: (id) => request<ApprovalResponse>(`/investigations/${id}/approvals`),
  recordDecisions: (id, payload) =>
    request<ApprovalResponse>(`/investigations/${id}/approvals`, { method: "POST", body: payload }),

  // Jobs
  startRun: (id) => request<RunAccepted>(`/investigations/${id}/run`, { method: "POST" }),
  getJobStatus: (jobId) => request<JobStatusResponse>(`/jobs/${encodeURIComponent(jobId)}`),
  cancelJob: (jobId) => request<JobStatusResponse>(`/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" }),

  // Reports
  getReportPayload: (id) => request<ReportPayload>(`/reports/${id}`),
  downloadReportPdf: (id) => request<Blob>(`/reports/${id}.pdf`, { binary: true }),

  // Orgs
  listOrganizations: () => request<Organization[]>("/orgs"),
  createOrganization: (payload) =>
    request<Organization>("/orgs", { method: "POST", body: payload }),
  deprovisionOrganization: (id) =>
    request<void>(`/orgs/${id}`, { method: "DELETE" }),
};
