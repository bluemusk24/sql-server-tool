/**
 * Approval screen tests (#24).
 *
 * The screen lists each catalog query's SQL text, purpose, and expected
 * impact, collects per-query approve/reject decisions, keeps "Start run"
 * disabled until every query is approved, requires a reason of at least
 * 10 characters for a rejection and surfaces it, and flips read-only when
 * the API answers 409 (the run already started).
 */

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApprovalScreen } from "../pages/ApprovalScreen";
import { ApiError, services } from "../services";
import type {
  ApprovalQuery,
  ApprovalResponse,
  CatalogEntry,
  InvestigationResponse,
} from "../services/types";

const INVESTIGATION_ID = 7;

function entry(id: string, title: string): CatalogEntry {
  return {
    id,
    key: id,
    title,
    description: `${title} description`,
    purpose: `${title} purpose`,
    category: "performance",
    issue_area: "blocking",
    sql: `SELECT * FROM ${id}`,
    sql_text: `SELECT * FROM ${id}`,
    params: [],
    requires_approval: true,
    expected_impact: `${title} impact`,
    versions: ["2016"],
    min_permission: "VIEW SERVER STATE",
    read_only: true,
  };
}

const CATALOG: CatalogEntry[] = [
  entry("q1", "Blocking chains"),
  entry("q2", "Wait stats"),
];

const INVESTIGATION: InvestigationResponse = {
  id: INVESTIGATION_ID,
  status: "draft",
  catalog: CATALOG,
};

/** Build the API's approval view: cumulative approvals + rejections. */
function responseFor(
  approved: string[],
  rejected: { id: string; note: string }[] = [],
): ApprovalResponse {
  const queries: ApprovalQuery[] = CATALOG.map((catalogEntry) => {
    const rejection = rejected.find((r) => r.id === catalogEntry.id);
    return {
      catalog_id: catalogEntry.id,
      decision: approved.includes(catalogEntry.id)
        ? "approved"
        : rejection
          ? "rejected"
          : null,
      actor: "dba",
      note: rejection?.note ?? null,
      created_at: null,
    };
  });
  const state = approved.length === CATALOG.length
    ? "approved"
    : rejected.length > 0
      ? "rejected"
      : "pending";
  return { investigation_id: INVESTIGATION_ID, state, queries };
}

function renderApproval() {
  return render(
    <MemoryRouter initialEntries={[`/investigations/${INVESTIGATION_ID}/approve`]}>
      <Routes>
        <Route path="/investigations/:id/approve" element={<ApprovalScreen />} />
        <Route path="/jobs/:jobId" element={<div>job page</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

/** The `<details>` block that belongs to a catalog entry. */
function detailsFor(title: string): HTMLElement {
  const container = screen.getByText(title).closest("details");
  if (!container) throw new Error(`no <details> ancestor for "${title}"`);
  return container as HTMLElement;
}

describe("ApprovalScreen", () => {
  beforeEach(() => {
    vi.spyOn(services, "getInvestigation").mockResolvedValue(INVESTIGATION);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows each query's SQL text, purpose, and expected impact", async () => {
    renderApproval();

    expect(await screen.findByText("Blocking chains purpose")).toBeTruthy();
    expect(screen.getByText("Wait stats purpose")).toBeTruthy();
    expect(screen.getByText("Blocking chains impact")).toBeTruthy();
    expect(screen.getByText("Wait stats impact")).toBeTruthy();
    expect(screen.getByText("SELECT * FROM q1")).toBeTruthy();
    expect(screen.getByText("SELECT * FROM q2")).toBeTruthy();
  });

  it("keeps Start run disabled until every query is approved", async () => {
    const approved: string[] = [];
    vi.spyOn(services, "recordDecisions").mockImplementation(
      async (_id, payload) => {
        const [catalogId, decision] = Object.entries(payload.decisions)[0];
        if (decision === "approved") approved.push(catalogId);
        return responseFor(approved);
      },
    );

    renderApproval();
    await screen.findByText("Blocking chains purpose");

    const start = screen.getByRole("button", { name: /start run/i });
    expect(start).toBeDisabled();

    fireEvent.click(
      within(detailsFor("Blocking chains")).getByRole("radio", { name: /approve/i }),
    );
    await screen.findByText("1 of 2 approved");
    expect(start).toBeDisabled(); // one still pending

    fireEvent.click(
      within(detailsFor("Wait stats")).getByRole("radio", { name: /approve/i }),
    );
    await screen.findByText("2 of 2 approved");
    expect(start).toBeEnabled();
  });

  it("requires a reason of at least 10 characters for a rejection", async () => {
    const record = vi.spyOn(services, "recordDecisions").mockImplementation(
      async (_id, payload) => {
        const [catalogId] = Object.entries(payload.decisions)[0];
        return responseFor([], [{ id: catalogId, note: payload.note ?? "rejected" }]);
      },
    );

    renderApproval();
    await screen.findByText("Blocking chains purpose");

    fireEvent.click(
      within(detailsFor("Blocking chains")).getByRole("radio", { name: /reject/i }),
    );

    const reason = await screen.findByLabelText(/reason/i);
    fireEvent.change(reason, { target: { value: "too short" } });
    expect(await screen.findByText(/reason required/i)).toBeTruthy();
    expect(record).not.toHaveBeenCalled(); // nothing sent until the reason qualifies

    fireEvent.change(reason, { target: { value: "blocks on tempdb" } });
    await waitFor(() => {
      expect(record).toHaveBeenCalledWith(INVESTIGATION_ID, {
        decisions: { q1: "rejected" },
        note: "blocks on tempdb",
      });
    });

    // The rejection is surfaced and Start run stays disabled.
    expect(await screen.findByText(/rejected: blocks on tempdb/)).toBeTruthy();
    expect(screen.getByRole("button", { name: /start run/i })).toBeDisabled();
  });

  it("starts the run and navigates to the job page once all are approved", async () => {
    const approved: string[] = [];
    vi.spyOn(services, "recordDecisions").mockImplementation(
      async (_id, payload) => {
        const [catalogId] = Object.entries(payload.decisions)[0];
        approved.push(catalogId);
        return responseFor(approved);
      },
    );
    const startRun = vi
      .spyOn(services, "startRun")
      .mockResolvedValue({ job_id: "job-42" });

    renderApproval();
    await screen.findByText("Blocking chains purpose");

    fireEvent.click(
      within(detailsFor("Blocking chains")).getByRole("radio", { name: /approve/i }),
    );
    fireEvent.click(
      within(detailsFor("Wait stats")).getByRole("radio", { name: /approve/i }),
    );

    const start = await screen.findByRole("button", { name: /start run/i });
    await waitFor(() => expect(start).toBeEnabled());
    fireEvent.click(start);

    expect(await screen.findByText(/job page/)).toBeTruthy();
    expect(startRun).toHaveBeenCalledWith(INVESTIGATION_ID);
  });

  it("goes read-only when the API reports the run already started (409)", async () => {
    vi.spyOn(services, "recordDecisions").mockRejectedValue(
      new ApiError(409, "Run already started", { detail: "Run already started" }),
    );

    renderApproval();
    await screen.findByText("Blocking chains purpose");

    fireEvent.click(
      within(detailsFor("Blocking chains")).getByRole("radio", { name: /approve/i }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(/read.?only/i);
    expect(
      within(detailsFor("Blocking chains")).getByRole("radio", { name: /approve/i }),
    ).toBeDisabled();
    expect(screen.getByRole("button", { name: /start run/i })).toBeDisabled();
  });
});
