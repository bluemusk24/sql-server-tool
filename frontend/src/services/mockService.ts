/**
 * Mock implementation of ServiceClient.
 *
 * Holds all state in memory so the entire UI runs without a backend.
 * Each method mimics the shape and latency of the real API. Switch
 * between mock and real via `VITE_USE_MOCK` (defaults to true).
 */

import { MOCK_CATALOG, MOCK_REPORT } from "./mockData";
import { ApiError } from "./http";
import type {
  ApprovalRequest,
  ApprovalResponse,
  CreateInvestigationRequest,
  Credentials,
  CreateOrganizationRequest,
  HealthStatus,
  InvestigationResponse,
  InvestigationSummary,
  JobStatusResponse,
  LoginRequest,
  Organization,
  ReportPayload,
  RunAccepted,
  Session,
  Decision,
} from "./types";
import type { ServiceClient } from "./serviceClient";

const NETWORK_DELAY_MS = 150;

function delay<T>(value: T, ms = NETWORK_DELAY_MS): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

// ── In-memory state ─────────────────────────────────────────────────

let nextInvestigationId = 100;
let nextOrgId = 10;

// Seeded users — mirror the backend's credentials so the same
// username/password works in mock and real mode.
const MOCK_USERS: Record<string, { password: string; session: Session }> = {
  dba: { password: "dba-password", session: { organization: "Acme Corp", actor: "dba" } },
  admin: { password: "admin-password", session: { organization: "Acme Corp", actor: "admin" } },
};

// The mock starts signed out so the login screen appears; `login`
// populates this and `logout` clears it.
let currentSession: Session | null = null;

const investigations = new Map<number, InvestigationResponse>();
const approvals = new Map<number, ApprovalResponse>();
const jobs = new Map<string, JobStatusResponse>();
const orgs = new Map<number, Organization>([
  [1, { id: 1, name: "Acme Corp", issuer: "https://idp.acme.example" }],
  [2, { id: 2, name: "Globex Inc", issuer: "https://idp.globex.example" }],
]);

// Seed one investigation so the dashboard isn't empty on first load
const seedInvestigation: InvestigationResponse = {
  id: 42,
  status: "approved",
  server_host: "sql01.acme.example",
  server_port: 1433,
  auth_type: "sql",
  databases: null,
  lookback_hours: 4,
  created_at: "2026-10-12T13:55:00Z",
  catalog: MOCK_CATALOG,
};
investigations.set(42, seedInvestigation);
approvals.set(42, {
  investigation_id: 42,
  state: "approved",
  queries: MOCK_CATALOG.filter((q) => q.requires_approval).map((q) => ({
    catalog_id: q.id,
    decision: "approved" as Decision,
    actor: "dina",
    note: null,
    created_at: "2026-10-12T14:00:00Z",
  })),
});

// ── Mock service ────────────────────────────────────────────────────

export const mockServiceClient: ServiceClient = {
  // Auth
  async login(credentials: LoginRequest): Promise<Session> {
    await delay(null, 200);
    const user = MOCK_USERS[credentials.username];
    if (!user || user.password !== credentials.password) {
      throw new ApiError(401, "Invalid username or password");
    }
    currentSession = user.session;
    return user.session;
  },
  logout(): void {
    currentSession = null;
  },
  async getSession(): Promise<Session> {
    if (!currentSession) throw new ApiError(401, "Not authenticated");
    return delay(currentSession);
  },

  // Health
  async getHealth(): Promise<HealthStatus> {
    return delay({ status: "ok", version: "0.1.0" });
  },

  // Investigations
  async listInvestigations(): Promise<InvestigationSummary[]> {
    return delay(
      Array.from(investigations.values()).map((i) => ({
        id: i.id,
        status: i.status,
        server_host: i.server_host,
        server_port: i.server_port,
        auth_type: i.auth_type,
        databases: i.databases,
        lookback_hours: i.lookback_hours,
        created_at: i.created_at,
      })),
    );
  },
  async getInvestigation(id: number): Promise<InvestigationResponse> {
    const found = investigations.get(id);
    if (!found) throw new Error(`no investigation ${id}`);
    return delay(found);
  },
  async createInvestigation(payload: CreateInvestigationRequest): Promise<InvestigationResponse> {
    const id = nextInvestigationId++;
    const record: InvestigationResponse = {
      id,
      status: "draft",
      server_host: payload.server_host,
      server_port: payload.server_port ?? 1433,
      auth_type: payload.auth_type,
      databases: payload.databases ?? null,
      lookback_hours: payload.lookback_hours,
      created_at: new Date().toISOString(),
      catalog: MOCK_CATALOG,
    };
    investigations.set(id, record);
    approvals.set(id, {
      investigation_id: id,
      state: "pending",
      queries: MOCK_CATALOG.filter((q) => q.requires_approval).map((q) => ({
        catalog_id: q.id,
        decision: null,
        actor: null,
        note: null,
        created_at: null,
      })),
    });
    void payload;
    return delay(record);
  },

  async deleteInvestigation(id: number): Promise<void> {
    if (!investigations.has(id)) throw new Error(`no investigation ${id}`);
    investigations.delete(id);
    approvals.delete(id);
    for (const [jobId, job] of jobs) {
      if (job.investigation_id === id) jobs.delete(jobId);
    }
    await delay(null);
  },

  // Approvals
  async getApprovals(investigationId: number): Promise<ApprovalResponse> {
    const found = approvals.get(investigationId);
    if (!found) throw new Error(`no approvals for ${investigationId}`);
    return delay(found);
  },
  async recordDecisions(investigationId: number, payload: ApprovalRequest): Promise<ApprovalResponse> {
    const existing = approvals.get(investigationId);
    if (!existing) throw new Error(`no approvals for ${investigationId}`);

    const queries = existing.queries.map((q) => {
      const decision = payload.decisions[q.catalog_id];
      if (!decision) return q;
      return {
        ...q,
        decision,
        actor: "dina",
        note: decision === "rejected" ? payload.note ?? null : null,
        created_at: "2026-10-12T15:00:00Z",
      };
    });

    const allApproved = queries.every((q) => q.decision === "approved");
    const anyRejected = queries.some((q) => q.decision === "rejected");
    const state = anyRejected ? "rejected" : allApproved ? "approved" : "pending";

    const updated: ApprovalResponse = { investigation_id: investigationId, state, queries };
    approvals.set(investigationId, updated);

    const inv = investigations.get(investigationId);
    if (inv) investigations.set(investigationId, { ...inv, status: state });

    return delay(updated);
  },

  // Jobs
  async startRun(investigationId: number, _credentials?: Credentials | null): Promise<RunAccepted> {
    const jobId = `job-${Date.now()}`;
    jobs.set(jobId, {
      job_id: jobId,
      status: "running",
      investigation_id: investigationId,
      result: null,
      error: null,
    });
    return delay({ job_id: jobId });
  },
  async getJobStatus(jobId: string): Promise<JobStatusResponse> {
    const found = jobs.get(jobId);
    if (!found) throw new Error(`no job ${jobId}`);
    return delay(found);
  },
  async cancelJob(jobId: string): Promise<JobStatusResponse> {
    const found = jobs.get(jobId);
    if (!found) throw new Error(`no job ${jobId}`);
    const updated = { ...found, status: "canceled" as const };
    jobs.set(jobId, updated);
    return delay(updated);
  },

  // Reports
  async getReportPayload(_investigationId: number): Promise<ReportPayload> {
    return delay(MOCK_REPORT);
  },
  async downloadReportPdf(_investigationId: number): Promise<Blob> {
    return delay(new Blob([`%PDF-1.4\n%mock report for investigation ${_investigationId}\n`], { type: "application/pdf" }));
  },

  // Orgs
  async listOrganizations(): Promise<Organization[]> {
    return delay(Array.from(orgs.values()));
  },
  async createOrganization(payload: CreateOrganizationRequest): Promise<Organization> {
    const id = nextOrgId++;
    const org: Organization = { id, ...payload };
    orgs.set(id, org);
    return delay(org);
  },
  async deprovisionOrganization(id: number): Promise<void> {
    orgs.delete(id);
    return delay(undefined);
  },
};
