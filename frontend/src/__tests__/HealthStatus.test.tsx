/**
 * API health indicator test (#22).
 *
 * The indicator calls the real `GET /health` endpoint and
 * shows its status; the fetch is mocked to assert the
 * displayed value.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { HealthStatus } from "../components/HealthStatus";

describe("HealthStatus", () => {
  it("shows the API status returned by GET /health", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ status: "ok", version: "0.1.0" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    render(<HealthStatus />);

    await waitFor(() => {
      expect(screen.getByText(/api status: ok/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/\(v0\.1\.0\)/)).toBeInTheDocument();
  });

  it("shows an unavailable status when the API cannot be reached", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(null, { status: 503 })),
    );

    render(<HealthStatus />);

    await waitFor(() => {
      expect(screen.getByText(/api status: unavailable/i)).toBeInTheDocument();
    });
  });
});
