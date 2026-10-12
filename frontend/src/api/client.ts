/**
 * Typed API client (#22).
 *
 * One module for every endpoint the screens (#23–#25) need. Every
 * request carries the session cookie (`credentials: "include"`, the
 * #30 session) and is capped by a timeout so a stalled API cannot
 * hang the UI. The base URL comes from `VITE_API_BASE_URL`.
 */

import type {
  ApprovalRequest,
  ApprovalResponse,
  AuthorizationStart,
  CreateInvestigationRequest,
  HealthStatus,
  InvestigationResponse,
  JobStatusResponse,
  RunAccepted,
  Session,
} from "./types";

/** A non-2xx response, carrying the HTTP status for callers to branch on. */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** Cap every request so a stalled API cannot hang the UI. */
const DEFAULT_TIMEOUT_MS = 10_000;

function baseUrl(): string {
  const configured = import.meta.env.VITE_API_BASE_URL;
  return typeof configured === "string" ? configured.replace(/\/+$/, "") : "";
}

function url(path: string): string {
  return `${baseUrl()}${path}`;
}

function isAbortError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    (error as { name?: string }).name === "AbortError"
  );
}

async function readErrorDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body?.detail === "string") {
      return body.detail;
    }
  } catch {
    // A non-JSON error body: fall back to the status text.
  }
  return response.statusText;
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  timeoutMs: number = DEFAULT_TIMEOUT_MS,
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url(path), {
      ...init,
      credentials: "include",
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        ...(init.headers ?? {}),
      },
    });
    if (!response.ok) {
      throw new ApiError(response.status, await readErrorDetail(response));
    }
    return (await response.json()) as T;
  } catch (error) {
    if (isAbortError(error)) {
      throw new ApiError(408, "request timed out");
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

/** GET /health (#2) — the API liveness/version indicator. */
export function getHealth(): Promise<HealthStatus> {
  return request<HealthStatus>("/health");
}

/** GET /auth/session — the session indicator (#30 provides it). */
export function getSession(): Promise<Session> {
  return request<Session>("/auth/session");
}

/** GET /auth/authorize — start the OIDC flow (#30 provides it). */
export function startAuthorization(): Promise<AuthorizationStart> {
  return request<AuthorizationStart>("/auth/authorize");
}

/** POST /investigations (#9). */
export function createInvestigation(
  payload: CreateInvestigationRequest,
): Promise<InvestigationResponse> {
  return request<InvestigationResponse>("/investigations", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/** POST /investigations/{id}/approvals (#10). */
export function recordDecisions(
  investigationId: number,
  payload: ApprovalRequest,
): Promise<ApprovalResponse> {
  return request<ApprovalResponse>(
    `/investigations/${investigationId}/approvals`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
  );
}

/** POST /investigations/{id}/run — enqueue the diagnostic run (#11). */
export function startRun(investigationId: number): Promise<RunAccepted> {
  return request<RunAccepted>(`/investigations/${investigationId}/run`, {
    method: "POST",
  });
}

/** GET /jobs/{job_id} (#11). */
export function getJobStatus(jobId: string): Promise<JobStatusResponse> {
  return request<JobStatusResponse>(`/jobs/${encodeURIComponent(jobId)}`);
}

/**
 * GET /reports/{id}.pdf (#20) — the incident report as a binary blob.
 *
 * Separate from `request` because the response is a PDF, not JSON.
 */
export async function downloadReport(
  investigationId: number,
): Promise<Blob> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS);
  try {
    const response = await fetch(
      url(`/reports/${investigationId}.pdf`),
      {
        credentials: "include",
        signal: controller.signal,
      },
    );
    if (!response.ok) {
      throw new ApiError(
        response.status,
        await readErrorDetail(response),
      );
    }
    return await response.blob();
  } catch (error) {
    if (isAbortError(error)) {
      throw new ApiError(408, "request timed out");
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}
