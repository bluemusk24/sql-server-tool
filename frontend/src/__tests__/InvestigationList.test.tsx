/**
 * Investigation list (#34): every row names the SQL Server it targets,
 * with its status and the right next-step link.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { InvestigationList } from "../components/InvestigationList";
import { ApiError, services } from "../services";
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

  it("renders a named instance bare, without a port suffix (#37)", async () => {
    vi.spyOn(services, "listInvestigations").mockResolvedValue([
      summary({ id: 3, server_host: "ATADBETSP001\\OISSQL" }),
    ]);

    render(<InvestigationList />, { wrapper: MemoryRouter });

    expect(await screen.findByText("ATADBETSP001\\OISSQL")).toBeTruthy();
    expect(screen.queryByText(/OISSQL:1433/)).toBeNull();
  });

  it("shows the empty state", async () => {
    vi.spyOn(services, "listInvestigations").mockResolvedValue([]);

    render(<InvestigationList />, { wrapper: MemoryRouter });

    expect(await screen.findByText("No investigations yet.")).toBeTruthy();
  });

  it("deletes a row after confirmation and keeps the rest (#39)", async () => {
    vi.spyOn(services, "listInvestigations").mockResolvedValue([
      summary({ id: 1 }),
      summary({ id: 2, server_host: "sql-dr-02.corp.example" }),
    ]);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const del = vi
      .spyOn(services, "deleteInvestigation")
      .mockResolvedValue(undefined);

    render(<InvestigationList />, { wrapper: MemoryRouter });
    await screen.findByText("sql-prod-01.corp.example:1433");

    fireEvent.click(screen.getAllByRole("button", { name: "Delete" })[0]);

    expect(window.confirm).toHaveBeenCalledTimes(1);
    expect(del).toHaveBeenCalledWith(1);
    await waitFor(() =>
      expect(screen.queryByText("sql-prod-01.corp.example:1433")).toBeNull(),
    );
    expect(await screen.findByText("sql-dr-02.corp.example:1433")).toBeTruthy();
  });

  it("does nothing when the confirmation is declined", async () => {
    vi.spyOn(services, "listInvestigations").mockResolvedValue([
      summary({ id: 1 }),
    ]);
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const del = vi
      .spyOn(services, "deleteInvestigation")
      .mockResolvedValue(undefined);

    render(<InvestigationList />, { wrapper: MemoryRouter });
    await screen.findByText("sql-prod-01.corp.example:1433");

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    expect(del).not.toHaveBeenCalled();
    expect(
      await screen.findByText("sql-prod-01.corp.example:1433"),
    ).toBeTruthy();
  });

  it("keeps the row and surfaces the error when deletion fails", async () => {
    vi.spyOn(services, "listInvestigations").mockResolvedValue([
      summary({ id: 1 }),
    ]);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.spyOn(services, "deleteInvestigation").mockRejectedValue(
      new ApiError(409, "A run is in progress for this investigation"),
    );

    render(<InvestigationList />, { wrapper: MemoryRouter });
    await screen.findByText("sql-prod-01.corp.example:1433");

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("A run is in progress");
    expect(
      screen.getByText("sql-prod-01.corp.example:1433"),
    ).toBeTruthy();
  });
});
