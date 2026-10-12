/**
 * Login screen test (#22).
 *
 * The "Sign in with SSO" button hits `GET /auth/authorize`.
 * Until #30 deploys that endpoint it returns 404, and the
 * screen must show a clear notice rather than crashing.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../auth/AuthContext";
import { LoginPage } from "../pages/LoginPage";

describe("LoginPage", () => {
  it("hits /auth/authorize and tolerates a 404 with a clear notice", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(null, { status: 404 }));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <AuthProvider>
        <MemoryRouter>
          <LoginPage />
        </MemoryRouter>
      </AuthProvider>,
    );

    fireEvent.click(
      await screen.findByRole("button", { name: /sign in with sso/i }),
    );

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/not deployed/i);
    });

    // The button really did call GET /auth/authorize.
    const paths = fetchMock.mock.calls.map((call) => call[0]);
    expect(paths).toContain("/auth/authorize");
  });
});
