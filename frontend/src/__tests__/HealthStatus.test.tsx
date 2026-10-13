/**
 * API health indicator test (#22).
 *
 * The indicator calls `services.getHealth()` and shows its status.
 * The service call is spied on to assert the displayed value for both
 * the healthy and the unreachable cases.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { HealthStatus } from "../components/HealthStatus";
import { services } from "../services";

describe("HealthStatus", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the API status returned by getHealth", async () => {
    vi.spyOn(services, "getHealth").mockResolvedValue({
      status: "ok",
      version: "0.1.0",
    });

    render(<HealthStatus />);

    await waitFor(() => {
      expect(screen.getByText(/api status: ok/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/\(v0\.1\.0\)/)).toBeInTheDocument();
  });

  it("shows an unavailable status when the health check fails", async () => {
    vi.spyOn(services, "getHealth").mockRejectedValue(
      new Error("connection refused"),
    );

    render(<HealthStatus />);

    await waitFor(() => {
      expect(screen.getByText(/api status: unavailable/i)).toBeInTheDocument();
    });
  });
});
