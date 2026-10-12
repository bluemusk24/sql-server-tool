/**
 * Low-level HTTP transport for the services layer.
 *
 * Every real (non-mock) service call goes through `request`, which
 * centralises the base-URL resolution, session-cookie forwarding,
 * timeout handling, and ApiError construction. Components never call
 * `fetch` directly — they go through a `ServiceClient` method.
 */

const DEFAULT_TIMEOUT_MS = 15_000;

/** A non-2xx response, carrying the HTTP status and parsed body. */
export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, message: string, body?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

function baseUrl(): string {
  const configured = import.meta.env.VITE_API_BASE_URL;
  return typeof configured === "string" ? configured.replace(/\/+$/, "") : "";
}

export function apiUrl(path: string): string {
  return `${baseUrl()}${path}`;
}

function isAbortError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    (error as { name?: string }).name === "AbortError"
  );
}

async function readError(
  response: Response,
): Promise<{ message: string; body: unknown }> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    body = undefined;
  }
  if (typeof body === "object" && body !== null && "detail" in body) {
    const detail = (body as { detail?: unknown }).detail;
    if (typeof detail === "string") return { message: detail, body };
  }
  return { message: response.statusText, body };
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  /** Response is binary (PDF) rather than JSON. */
  binary?: boolean;
}

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, binary = false } = opts;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS);
  try {
    const response = await fetch(apiUrl(path), {
      method,
      credentials: "include",
      signal: controller.signal,
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    if (!response.ok) {
      const { message, body: errBody } = await readError(response);
      throw new ApiError(response.status, message, errBody);
    }
    if (binary) return (await response.blob()) as T;
    return (await response.json()) as T;
  } catch (error) {
    if (isAbortError(error)) throw new ApiError(408, "request timed out");
    throw error;
  } finally {
    clearTimeout(timer);
  }
}
