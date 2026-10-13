/**
 * Login screen test (#22).
 *
 * The form posts username/password to `services.login`. An invalid
 * login (401) surfaces a clear message rather than crashing, and a
 * valid login calls the service with the entered credentials.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../auth/AuthContext";
import { LoginPage } from "../pages/LoginPage";
import { services, ApiError } from "../services";

function renderLogin() {
  return render(
    <AuthProvider>
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>
    </AuthProvider>,
  );
}

async function fillAndSubmit(username: string, password: string) {
  fireEvent.change(await screen.findByLabelText(/username/i), {
    target: { value: username },
  });
  fireEvent.change(screen.getByLabelText(/password/i), {
    target: { value: password },
  });
  fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
}

describe("LoginPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows a clear message when credentials are rejected", async () => {
    vi.spyOn(services, "login").mockRejectedValue(
      new ApiError(401, "Invalid username or password"),
    );

    renderLogin();
    await fillAndSubmit("dba", "wrong-password");

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(
        /invalid username or password/i,
      );
    });
  });

  it("calls login with the entered credentials", async () => {
    const loginMock = vi.spyOn(services, "login").mockResolvedValue({
      organization: "Acme Corp",
      actor: "dba",
    });

    renderLogin();
    await fillAndSubmit("dba", "dba-password");

    await waitFor(() => {
      expect(loginMock).toHaveBeenCalledWith({
        username: "dba",
        password: "dba-password",
      });
    });
  });
});
