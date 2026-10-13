/**
 * The single service-client interface.
 *
 * Every backend call the UI makes is a method on this interface.
 * Components depend only on `ServiceClient`, never on `fetch`, so the
 * real HTTP transport and the mock are interchangeable.
 */

import type {
  ApprovalRequest,
  ApprovalResponse,
  AuthorizationStart,
  CreateInvestigationRequest,
  CreateOrganizationRequest,
  Credentials,
  HealthStatus,
  InvestigationResponse,
  InvestigationSummary,
  JobStatusResponse,
  LoginRequest,
  Organization,
  ReportPayload,
  RunAccepted,
  Session,
} from "./types";

export interface ServiceClient {
  // Auth
  login(credentials: LoginRequest): Promise<Session>;
  logout(): void;
  getSession(): Promise<Session>;
  startAuthorization(): Promise<AuthorizationStart>;

  // Health
  getHealth(): Promise<HealthStatus>;

  // Investigations
  listInvestigations(): Promise<InvestigationSummary[]>;
  getInvestigation(id: number): Promise<InvestigationResponse>;
  createInvestigation(payload: CreateInvestigationRequest): Promise<InvestigationResponse>;

  // Approvals
  getApprovals(investigationId: number): Promise<ApprovalResponse>;
  recordDecisions(investigationId: number, payload: ApprovalRequest): Promise<ApprovalResponse>;

  // Jobs
  startRun(investigationId: number, credentials?: Credentials | null): Promise<RunAccepted>;
  getJobStatus(jobId: string): Promise<JobStatusResponse>;
  cancelJob(jobId: string): Promise<JobStatusResponse>;

  // Reports
  getReportPayload(investigationId: number): Promise<ReportPayload>;
  downloadReportPdf(investigationId: number): Promise<Blob>;

  // Orgs
  listOrganizations(): Promise<Organization[]>;
  createOrganization(payload: CreateOrganizationRequest): Promise<Organization>;
  deprovisionOrganization(id: number): Promise<void>;
}
