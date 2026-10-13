/**
 * Investigation list (#34): every row names the SQL Server it targets,
 * with its status and the right next-step link.
 */

import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { InvestigationList } from "../components/InvestigationList";
import { services } from "../services";
import type { InvestigationSummary } from "../services/types";

function summary(
  overrides: Partial<InvestigationSummary>,
): InvestigationSummary {
  return {
    id: 1,
    status: "draft",
    server_host: "sql-prod-01.corp.example",
    server_port: 1433,
    auth_type: "sql",
    databases: null,
    lookback_hours: 4,
    created_at: "2026-10-13T14:05:09Z",
    ...overrides,
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("InvestigationList", () => {
  it("names the target SQL Server for every investigation", async () => {
    vi.spyOn(services, "listInvestigations").mockResolvedValue([
      summary({ id: 1 }),
      summary({
        id: 2,
        status: "completed",
        server_host: "sql-dr-02.corp.example",
        server_port: 1434,
      }),
    ]);

    render(<InvestigationList />, { wrapper: MemoryRouter });

    expect(
      await screen.findByText("sql-prod-01.corp.example:1433"),
    ).toBeTruthy();
    expect(
      await screen.findByText("sql-dr-02.corp.example:1434"),
    ).toBeTruthy();
    expect(screen.getByText("draft")).toBeTruthy();
    expect(screen.getByText("completed")).toBeTruthy();

    // Status drives the next-step link: approvals for open runs, the
    // report for finished ones.
    expect(
      screen.getByRole("link", { name: /review approvals/i }),
    ).toBeTruthy();
    expect(screen.getByRole("link", { name: /view report/i })).toBeTruthy();
  });

  it("shows the empty state", async () => {
    vi.spyOn(services, "listInvestigations").mockResolvedValue([]);

    render(<InvestigationList />, { wrapper: MemoryRouter });

    expect(await screen.findByText("No investigations yet.")).toBeTruthy();
  });
});
