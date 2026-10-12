/**
 * Protected-page tests (#22).
 *
 * Unauthenticated access to `/` redirects to the login
 * screen; a resolved session renders the landing page
 * instead. The session indicator (`GET /auth/session`)
 * is mocked so no backend is needed.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../auth/AuthContext";
import { ProtectedRoute } from "../auth/ProtectedRoute";
import { LoginPage } from "../pages/LoginPage";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderAt(path: string, fetchMock: ReturnType<typeof vi.fn>) {
  vi.stubGlobal("fetch", fetchMock);
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={<ProtectedRoute />} />
        </Routes>
      </MemoryRouter>
    </AuthProvider>,
  );
}

describe("protected page", () => {
  it("redirects unauthenticated access to / to the login screen", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(null, { status: 404 }));
    renderAt("/", fetchMock);

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /sign in with sso/i }),
      ).toBeInTheDocument();
    });
    // The landing page's organization line must not be present.
    expect(screen.queryByText(/organization:/i)).not.toBeInTheDocument();
  });

  it("renders the landing page when a session exists", async () => {
    const fetchMock = vi.fn((input: string) => {
      if (input === "/auth/session") {
        return Promise.resolve(
          jsonResponse({ organization: "Acme Corp", actor: "dina" }),
        );
      }
      if (input === "/health") {
        return Promise.resolve(
          jsonResponse({ status: "ok", version: "0.1.0" }),
        );
      }
      return Promise.resolve(new Response(null, { status: 404 }));
    });
    renderAt("/", fetchMock);

    await waitFor(() => {
      expect(
        screen.getByText(/organization: acme corp/i),
      ).toBeInTheDocument();
    });
    expect(screen.getByText(/signed in as dina/i)).toBeInTheDocument();
  });
});
