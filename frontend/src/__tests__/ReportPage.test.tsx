/**
 * Report page tests (#25).
 *
 * Every required section must render from a fixture payload: summary,
 * data-source collection status, primary and alternative causes, findings
 * by area with severity/confidence, raw evidence, coverage gaps, validation
 * checks, remediation steps, the read-only disclaimer, and the PDF button.
 * A 404 surfaces the "no report yet" message instead of crashing.
 */

import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ReportPage } from "../pages/ReportPage";
import { ApiError, services } from "../services";
import type { ReportPayload } from "../services/types";

const FIXTURE: ReportPayload = {
  investigation_id: 1,
  organization: "McDermott",
  server_host: "sql-prod-01",
  server_port: 1433,
  database_scope: "databases",
  databases: ["Orders", "Ledger"],
  lookback_hours: 8,
  investigation_time: "2026-10-13T10:00:00Z",
  data_sources: [
    {
      source_id: "blocking",
      title: "Blocking chains",
      category: "performance",
      issue_area: "blocking",
      status: "collected",
      checked: true,
      note: "",
    },
    {
      source_id: "agent",
      title: "SQL Agent jobs",
      category: "operations",
      issue_area: "agent",
      status: "unavailable",
      checked: false,
      note: "missing VIEW SERVER STATE",
    },
  ],
  findings_by_area: [
    {
      area: "blocking",
      severity: "high",
      confidence_percent: 80,
      confidence_label: "high",
      signal_count: 1,
      findings: [
        {
          rule_id: "blk-1",
          area: "blocking",
          severity: "high",
          title: "Blocking session 71",
          description: "Head blocker holds locks on Orders",
          evidence_ids: ["ev-1"],
        },
      ],
      evidence: [
        {
          evidence_id: "ev-1",
          area: "blocking",
          timestamp: "2026-10-13T09:59:00Z",
          description: "sys.dm_exec_requests snapshot",
          details: { wait_type: "LCK_M_X", head_blocker: 71 },
        },
      ],
    },
  ],
  primary_cause: {
    title: "Head blocker on Orders",
    description: "Session 71 holds long-running locks",
    confidence_percent: 80,
    confidence_label: "high",
  },
  alternatives: [
    {
      title: "Tempdb contention",
      description: "Possible secondary contributor",
      confidence_percent: 40,
      confidence_label: "low",
    },
  ],
  confidence_percent: 80,
  confidence_label: "high",
  severity: "high",
  rationale: "Lock waits dominate the lookback window",
  inconclusive: false,
  coverage_gaps: [
    {
      source_id: "agent",
      issue_area: "agent",
      status: "unavailable",
      note: "Agent job history not readable",
    },
  ],
  recommendations: [
    {
      area: "blocking",
      validation_checks: ["Confirm session 71 is still blocking"],
      remediation: [
        {
          step: "Identify the application owner of session 71",
          draft_sql: null,
          disclaimer: "Read-only guidance",
        },
      ],
      disclaimer: "Validate before changing anything",
    },
  ],
  validation_checks: ["Re-check the head blocker after peak hours"],
  remediation: [
    {
      step: "Coordinate a blocking fix with the application team",
      draft_sql: null,
      disclaimer: "Read-only",
    },
  ],
  read_only_disclaimer: "This tool never executes changes on the server",
};

function renderReport() {
  return render(
    <MemoryRouter initialEntries={["/investigations/1/report"]}>
      <Routes>
        <Route path="/investigations/:id/report" element={<ReportPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ReportPage", () => {
  beforeEach(() => {
    vi.spyOn(services, "getReportPayload").mockResolvedValue(FIXTURE);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders every required section from the fixture payload", async () => {
    renderReport();

    // Summary
    expect(await screen.findByText("Diagnostic report")).toBeTruthy();
    expect(screen.getByText("sql-prod-01:1433")).toBeTruthy();
    expect(screen.getByText("8 hours")).toBeTruthy();
    expect(screen.getByTestId("report-severity")).toHaveTextContent("high");

    // Data-source collection status (incl. the not-checked/unavailable one)
    expect(screen.getByText("Blocking chains")).toBeTruthy();
    expect(screen.getByText(/SQL Agent jobs/)).toBeTruthy();
    expect(screen.getByText(/unavailable, not checked/)).toBeTruthy();
    expect(screen.getByText(/missing VIEW SERVER STATE/)).toBeTruthy();

    // Causes
    expect(screen.getByText("Head blocker on Orders")).toBeTruthy();
    expect(screen.getByText("Tempdb contention")).toBeTruthy();

    // Findings by area: severity + confidence heading, and the signal itself
    expect(
      screen.getByRole("heading", { level: 3, name: /blocking \(severity: high\)/ }),
    ).toBeTruthy();
    expect(screen.getByText("Blocking session 71")).toBeTruthy();
    expect(screen.getByText(/Head blocker holds locks on Orders/)).toBeTruthy();

    // Raw evidence, including the JSON details blob
    expect(screen.getByText("sys.dm_exec_requests snapshot")).toBeTruthy();
    expect(screen.getByText(/LCK_M_X/)).toBeTruthy();

    // Coverage gaps
    expect(screen.getByText(/Agent job history not readable/)).toBeTruthy();

    // Validation checks (top-level and per recommendation)
    expect(
      screen.getByText("Re-check the head blocker after peak hours"),
    ).toBeTruthy();
    expect(screen.getByText("Confirm session 71 is still blocking")).toBeTruthy();

    // Remediation + read-only disclaimer + PDF button
    expect(
      screen.getByText("Identify the application owner of session 71"),
    ).toBeTruthy();
    expect(
      screen.getByText(/This tool never executes changes on the server/),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: /download pdf/i })).toBeTruthy();
  });

  it("hides optional sections the payload leaves empty", async () => {
    const empty: ReportPayload = {
      ...FIXTURE,
      data_sources: [],
      coverage_gaps: [],
      validation_checks: [],
      recommendations: [],
    };
    vi.spyOn(services, "getReportPayload").mockResolvedValue(empty);

    renderReport();
    expect(await screen.findByText("Diagnostic report")).toBeTruthy();

    expect(screen.queryByText("Coverage gaps")).toBeNull();
    expect(screen.queryByText("Validation checks")).toBeNull();
    expect(screen.queryByText("Recommendations")).toBeNull();
    // Required sections are still there.
    expect(screen.getByText("Primary cause")).toBeTruthy();
  });

  it("shows a clear message when no report exists yet (404)", async () => {
    vi.spyOn(services, "getReportPayload").mockRejectedValue(
      new ApiError(404, "Not found", { detail: "No report" }),
    );

    renderReport();
    expect(
      await screen.findByText(/no report for this investigation yet/i),
    ).toBeTruthy();
  });

  it("labels a synthetic report so sample data is never mistaken (#26)", async () => {
    vi.spyOn(services, "getReportPayload").mockResolvedValue({
      ...FIXTURE,
      synthetic: true,
    });

    renderReport();
    const banner = await screen.findByTestId("synthetic-banner");
    expect(banner.textContent).toContain("not collected from a real server");
  });

  it("shows no synthetic label on a real (or empty) report", async () => {
    renderReport();
    expect(await screen.findByText("Diagnostic report")).toBeTruthy();
    expect(screen.queryByTestId("synthetic-banner")).toBeNull();
  });
});
