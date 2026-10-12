/**
 * Typed API client tests (#22).
 *
 * Every request carries the session cookie
 * (`credentials: "include"`) and targets the right
 * endpoint; non-2xx responses raise an `ApiError`
 * carrying the HTTP status.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, createInvestigation, getJobStatus } from "../api/client";

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
});

function jsonResponse(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("api client", () => {
  it("creates an investigation with credentials included", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ id: 1, status: "draft", catalog: [] }, 201),
    );

    const result = await createInvestigation({
      server_host: "sql01.acme.example",
      auth_type: "sql",
      lookback_hours: 4,
    });

    expect(result.id).toBe(1);
    expect(result.status).toBe("draft");
    expect(fetchMock).toHaveBeenCalledWith(
      "/investigations",
      expect.objectContaining({
        method: "POST",
        credentials: "include",
      }),
    );
  });

  it("fetches job status with credentials included", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        {
          job_id: "job-1",
          status: "running",
          investigation_id: 1,
          result: null,
          error: null,
        },
        200,
      ),
    );

    const status = await getJobStatus("job-1");

    expect(status.status).toBe("running");
    expect(fetchMock).toHaveBeenCalledWith(
      "/jobs/job-1",
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("raises an ApiError carrying the status on a non-ok response", async () => {
    fetchMock.mockResolvedValue(new Response("not found", { status: 404 }));

    await expect(getJobStatus("missing")).rejects.toMatchObject({
      name: "ApiError",
      status: 404,
    });
  });

  it("raises a timeout ApiError when the request is aborted", async () => {
    fetchMock.mockImplementation(
      () =>
        new Promise((_resolve, reject) => {
          const error = new Error("aborted");
          error.name = "AbortError";
          reject(error);
        }),
    );

    await expect(getJobStatus("slow")).rejects.toMatchObject({
      name: "ApiError",
      status: 408,
    });
  });

  it("is exported for the report download", () => {
    // downloadReport returns a Blob; its existence and error
    // type are part of the client surface used by #25.
    expect(ApiError).toBeDefined();
  });
});
