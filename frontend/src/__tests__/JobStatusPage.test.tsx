/**
 * Job status screen (#29): polls a run, offers cancel while it is in
 * flight, and links to the partial report when a run ended early.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { JobStatusPage } from "../pages/JobStatusPage";
import { services } from "../services";
import type { JobStatusResponse } from "../services/types";

function job(overrides: Partial<JobStatusResponse>): JobStatusResponse {
  return {
    job_id: "job-1",
    status: "running",
    investigation_id: 7,
    result: null,
    error: null,
    ...overrides,
  };
}

function renderPage() {
  render(
    <MemoryRouter initialEntries={["/jobs/job-1"]}>
      <Routes>
        <Route path="/jobs/:jobId" element={<JobStatusPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("JobStatusPage", () => {
  it("offers cancel while the run is in flight and stops on cancel (#29)", async () => {
    vi.spyOn(services, "getJobStatus")
      .mockResolvedValueOnce(job({ status: "running" }))
      .mockResolvedValue(job({ status: "canceled" }));
    const cancel = vi
      .spyOn(services, "cancelJob")
      .mockResolvedValue(job({ status: "canceled" }));

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /cancel run/i }));

    expect(cancel).toHaveBeenCalledWith("job-1");
    expect(await screen.findByText(/the run was canceled/i)).toBeTruthy();
    // The evidence the run managed to collect survived the cancel.
    expect(
      screen.getByRole("link", { name: /view partial report/i }),
    ).toBeTruthy();
  });

  it("surfaces a cancel failure and keeps watching the run", async () => {
    vi.spyOn(services, "getJobStatus").mockResolvedValue(
      job({ status: "running" }),
    );
    vi.spyOn(services, "cancelJob").mockRejectedValue(
      new Error("job already finished"),
    );

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /cancel run/i }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("job already finished");
    // Still watching: the cancel button comes back.
    expect(
      await screen.findByRole("button", { name: /cancel run/i }),
    ).toBeTruthy();
  });

  it("shows the timeout reason and the partial report link (#29)", async () => {
    vi.spyOn(services, "getJobStatus").mockResolvedValue(
      job({
        status: "failed",
        error: "run exceeded the overall timeout (600s)",
      }),
    );

    renderPage();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("overall timeout");
    expect(
      screen.getByRole("link", { name: /view partial report/i }),
    ).toBeTruthy();
  });

  it("offers the report link once the run succeeds", async () => {
    vi.spyOn(services, "getJobStatus").mockResolvedValue(
      job({ status: "succeeded", result: { investigation_id: 7 } }),
    );

    renderPage();

    expect(
      await screen.findByRole("link", { name: /view report/i }),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: /cancel run/i })).toBeNull();
  });
});
