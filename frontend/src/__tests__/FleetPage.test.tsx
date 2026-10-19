/**
 * Fleet page tests (#50).
 *
 * The multi-firm failure picture must render from the backend's
 * aggregate: totals, per-server findings by severity with refresh
 * failures called out, links into the latest report, recent failures,
 * and an honest empty state when the organization has investigated
 * nothing yet.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FleetPage } from "../pages/FleetPage";
import { ApiError, services } from "../services";
import type { FleetSummary } from "../services/types";

const FIXTURE: FleetSummary = {
  organization: "Acme Corp",
  generated_at: "2026-10-13T10:00:00Z",
  totals: {
    servers: 2,
    investigations: 3,
    findings: { high: 2, medium: 1, low: 0 },
    refresh_findings: 1,
  },
  servers: [
    {
      server_host: "sql01.acme.example",
      server_port: 1433,
      investigations: 2,
      last_investigation: "2026-10-13T10:00:00Z",
      findings_by_severity: { high: 1, medium: 1, low: 0 },
      total_findings: 2,
      refresh_findings: 1,
      areas: {
        refresh: { findings: 1, severity: "high" },
        agent_jobs: { findings: 1, severity: "medium" },
      },
      latest_report_id: 7,
      latest_primary_cause: "Data refresh / replication failure",
      latest_confidence_percent: 78,
      inconclusive: false,
    },
    {
      server_host: "sql-dr.acme.example",
      server_port: 1433,
      investigations: 1,
      last_investigation: "2026-10-11T09:10:00Z",
      findings_by_severity: { high: 1, medium: 0, low: 0 },
      total_findings: 1,
      refresh_findings: 0,
      areas: { blocking: { findings: 1, severity: "high" } },
      latest_report_id: 5,
      latest_primary_cause: "Blocking chain preventing query completion",
      latest_confidence_percent: 71,
      inconclusive: true,
    },
  ],
  recent_failures: [
    {
      investigation_id: 7,
      server_host: "sql01.acme.example",
      server_port: 1433,
      area: "refresh",
      severity: "high",
      database: "Sales",
      title: "Refresh job 'DW Refresh' failed at step 'Load FactSales'",
      time: "2026-10-13T10:00:00Z",
    },
  ],
};

const renderPage = () =>
  render(
    <MemoryRouter>
      <FleetPage />
    </MemoryRouter>,
  );

describe("FleetPage", () => {
  beforeEach(() => {
    vi.spyOn(services, "getFleet").mockResolvedValue(FIXTURE);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders totals, per-server findings, and the refresh column", async () => {
    renderPage();
    expect(await screen.findByTestId("fleet-page")).toBeInTheDocument();

    // Org-scoped heading.
    expect(
      screen.getByRole("heading", { name: /Fleet health — Acme Corp/ }),
    ).toBeInTheDocument();

    // Totals chips.
    expect(screen.getByText("2 server(s)")).toBeInTheDocument();
    expect(screen.getByText("1 refresh finding(s)")).toBeInTheDocument();

    // Both servers, ranked as the backend returned them.
    expect(
      screen.getByText("sql01.acme.example:1433"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("sql-dr.acme.example:1433"),
    ).toBeInTheDocument();

    // Refresh findings are visible per server and in the cause link.
    expect(screen.getByText("refresh: 1")).toBeInTheDocument();
    expect(screen.getByText("refresh: 0")).toBeInTheDocument();
    const causeLink = screen.getByRole("link", {
      name: /Data refresh \/ replication failure \(78%\)/,
    });
    expect(causeLink).toHaveAttribute(
      "href",
      "/investigations/7/report",
    );

    // The inconclusive server is labeled honestly.
    expect(screen.getByText("inconclusive")).toBeInTheDocument();

    // Recent failures name the database and link to the report.
    expect(
      screen.getByRole("link", {
        name: /Refresh job 'DW Refresh' failed/,
      }),
    ).toBeInTheDocument();
    expect(screen.getAllByText(/db: Sales|· Sales/).length).toBeGreaterThan(0);
  });

  it("shows the empty state when the organization has nothing yet", async () => {
    vi.spyOn(services, "getFleet").mockResolvedValue({
      ...FIXTURE,
      totals: {
        servers: 0,
        investigations: 0,
        findings: { high: 0, medium: 0, low: 0 },
        refresh_findings: 0,
      },
      servers: [],
      recent_failures: [],
    });
    renderPage();
    expect(
      await screen.findByText(/No investigations yet for Acme Corp/),
    ).toBeInTheDocument();
  });

  it("surfaces fetch errors instead of a blank page", async () => {
    vi.spyOn(services, "getFleet").mockRejectedValue(
      new ApiError(500, "boom"),
    );
    renderPage();
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("boom"));
  });
});
