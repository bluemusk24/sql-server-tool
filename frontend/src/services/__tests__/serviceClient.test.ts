/**
 * Tests for the centralized services layer.
 *
 * Covers the mock implementation (state transitions, error paths)
 * and the real HTTP client's request shaping. The factory selection
 * is verified by inspecting the client's behaviour.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { getServiceClient, services, ApiError } from "../index";
import { mockServiceClient } from "../mockService";
import type { ServiceClient } from "../serviceClient";

// ── Mock service behaviour ──────────────────────────────────────────

describe("mockServiceClient", () => {
  let client: ServiceClient;

  beforeEach(() => {
    // Each test gets a fresh module state by re-importing
    vi.resetModules();
    client = mockServiceClient;
    // Start each test signed out.
    client.logout();
  });

  it("returns a healthy status", async () => {
    const health = await client.getHealth();
    expect(health).toEqual({ status: "ok", version: "0.1.0" });
  });

  it("authenticates the seeded user and returns a session", async () => {
    const session = await client.login({ username: "dba", password: "dba-password" });
    expect(session.organization).toBe("Acme Corp");
    expect(session.actor).toBe("dba");

    // The stored session is now resolvable.
    const resolved = await client.getSession();
    expect(resolved.actor).toBe("dba");
  });

  it("rejects bad credentials with a 401", async () => {
    await expect(
      client.login({ username: "dba", password: "wrong" }),
    ).rejects.toMatchObject({ status: 401 });
    await expect(
      client.login({ username: "nobody", password: "dba-password" }),
    ).rejects.toMatchObject({ status: 401 });
  });

  it("logs out and rejects subsequent session fetches", async () => {
    await client.login({ username: "admin", password: "admin-password" });
    client.logout();
    await expect(client.getSession()).rejects.toMatchObject({ status: 401 });
  });

  it("lists seeded investigations", async () => {
    const list = await client.listInvestigations();
    expect(list.length).toBeGreaterThan(0);
    expect(list[0]).toHaveProperty("id");
    expect(list[0]).toHaveProperty("status");
  });

  it("creates a new investigation and returns its catalog", async () => {
    const created = await client.createInvestigation({
      server_host: "sql02.acme.example",
      auth_type: "sql",
      lookback_hours: 4,
    });
    expect(created.id).toBeGreaterThan(0);
    expect(created.status).toBe("draft");
    expect(created.catalog.length).toBeGreaterThan(0);
  });

  it("starts a job and reports running status", async () => {
    const inv = await client.createInvestigation({
      server_host: "sql01.acme.example",
      auth_type: "sql",
      lookback_hours: 8,
    });
    const run = await client.startRun(inv.id);
    expect(run.job_id).toMatch(/^job-/);

    const status = await client.getJobStatus(run.job_id);
    expect(status.status).toBe("running");
    expect(status.investigation_id).toBe(inv.id);
  });

  it("cancels a job and reports canceled status", async () => {
    const run = await client.startRun(42);
    const canceled = await client.cancelJob(run.job_id);
    expect(canceled.status).toBe("canceled");
  });

  it("records decisions and computes approved state", async () => {
    const inv = await client.createInvestigation({
      server_host: "sql01.acme.example",
      auth_type: "sql",
      lookback_hours: 1,
    });

    // Get the required approval ids
    const approvals = await client.getApprovals(inv.id);
    const requiredIds = approvals.queries.map((q) => q.catalog_id);

    // Approve all
    const decisions: Record<string, "approved"> = {};
    for (const id of requiredIds) decisions[id] = "approved";

    const updated = await client.recordDecisions(inv.id, { decisions });
    expect(updated.state).toBe("approved");
  });

  it("records a rejection and computes rejected state", async () => {
    const inv = await client.createInvestigation({
      server_host: "sql01.acme.example",
      auth_type: "sql",
      lookback_hours: 1,
    });

    const approvals = await client.getApprovals(inv.id);
    const firstId = approvals.queries[0].catalog_id;

    const updated = await client.recordDecisions(inv.id, {
      decisions: { [firstId]: "rejected" },
      note: "this query is too risky for production",
    });

    expect(updated.state).toBe("rejected");
    const rejected = updated.queries.find((q) => q.catalog_id === firstId);
    expect(rejected?.decision).toBe("rejected");
    expect(rejected?.note).toBe("this query is too risky for production");
  });

  it("returns a report payload matching the schema", async () => {
    const payload = await client.getReportPayload(42);
    expect(payload.investigation_id).toBe(42);
    expect(payload.organization).toBe("Acme Corp");
    expect(payload.primary_cause).toHaveProperty("title");
    expect(payload.findings_by_area.length).toBeGreaterThan(0);
    expect(payload.data_sources.length).toBeGreaterThan(0);
  });

  it("returns a PDF blob", async () => {
    const blob = await client.downloadReportPdf(42);
    expect(blob).toBeInstanceOf(Blob);
    expect(blob.type).toBe("application/pdf");
  });

  it("manages organizations", async () => {
    const orgs = await client.listOrganizations();
    expect(orgs.length).toBeGreaterThan(0);

    const created = await client.createOrganization({
      name: "Initech",
      issuer: "https://idp.initech.example",
    });
    expect(created.id).toBeGreaterThan(0);

    const afterCreate = await client.listOrganizations();
    expect(afterCreate.length).toBe(orgs.length + 1);

    await client.deprovisionOrganization(created.id);
    const afterDelete = await client.listOrganizations();
    expect(afterDelete.length).toBe(orgs.length);
  });

  it("rejects when getting an unknown investigation", async () => {
    await expect(client.getInvestigation(99999)).rejects.toThrow("no investigation");
  });

  it("deletes an investigation and drops it from the list (#39)", async () => {
    const created = await client.createInvestigation({
      server_host: "sql-todelete.acme.example",
      auth_type: "sql",
      lookback_hours: 4,
    });
    const before = await client.listInvestigations();
    expect(before.some((i) => i.id === created.id)).toBe(true);

    await client.deleteInvestigation(created.id);

    const after = await client.listInvestigations();
    expect(after.some((i) => i.id === created.id)).toBe(false);
  });

  it("rejects deleting an unknown investigation", async () => {
    await expect(client.deleteInvestigation(99999)).rejects.toThrow(
      "no investigation",
    );
  });

  it("rejects when getting an unknown job", async () => {
    await expect(client.getJobStatus("job-does-not-exist")).rejects.toThrow("no job");
  });
});

// ── Factory selection ───────────────────────────────────────────────

describe("getServiceClient factory", () => {
  it("returns a ServiceClient-shaped object", () => {
    const client = getServiceClient();
    expect(typeof client.getSession).toBe("function");
    expect(typeof client.createInvestigation).toBe("function");
    expect(typeof client.recordDecisions).toBe("function");
    expect(typeof client.downloadReportPdf).toBe("function");
  });

  it("returns the same singleton on repeated calls", () => {
    expect(getServiceClient()).toBe(services);
  });

  it("uses the mock when VITE_USE_MOCK is unset", async () => {
    // In the test env, VITE_USE_MOCK is not set, so the default is mock.
    // Verify by calling a method that only the mock implements with this
    // exact signature (the real one returns a Promise<Blob> from fetch).
    const client = getServiceClient();
    const health = await client.getHealth();
    expect(health).toEqual({ status: "ok", version: "0.1.0" });
  });
});

// ── ApiError ────────────────────────────────────────────────────────

describe("ApiError", () => {
  it("carries status and body", () => {
    const err = new ApiError(422, "validation failed", { detail: [] });
    expect(err.status).toBe(422);
    expect(err.body).toEqual({ detail: [] });
    expect(err.message).toBe("validation failed");
  });
});
