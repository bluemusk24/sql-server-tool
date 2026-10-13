/**
 * Connection & scope form tests (#23).
 *
 * Covers the acceptance criteria: inline validation messages on an empty
 * submit (with nothing sent), the lookback list coming from the shared
 * constant with a validator that still rejects a bad value, an exact payload
 * on a successful mocked submit (password never included), database-scope
 * splitting, 422 → field mapping, and Active Directory showing an
 * informational notice without blocking submission.
 */

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ConnectionScopeForm,
  validate,
  type FormState,
} from "../pages/ConnectionScopeForm";
import { ApiError, LOOKBACK_HOURS, services } from "../services";

const VALID_HOST = "sql02.acme.example";

function renderForm() {
  return render(
    <MemoryRouter initialEntries={["/investigations/new"]}>
      <Routes>
        <Route path="/investigations/new" element={<ConnectionScopeForm />} />
        <Route
          path="/investigations/:id/approve"
          element={<div>approval screen</div>}
        />
      </Routes>
    </MemoryRouter>,
  );
}

function submit() {
  fireEvent.click(screen.getByRole("button", { name: /create investigation/i }));
}

function fillRequired() {
  fireEvent.change(screen.getByLabelText(/server host/i), {
    target: { value: VALID_HOST },
  });
  fireEvent.change(screen.getByLabelText(/password/i), {
    target: { value: "s3cret-pass" },
  });
}

const validState: FormState = {
  serverHost: VALID_HOST,
  serverPort: "1433",
  username: "",
  authType: "sql",
  scope: "instance",
  databases: "",
  lookbackHours: 4,
};

describe("ConnectionScopeForm", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows inline validation messages and sends nothing on an empty submit", async () => {
    const create = vi.spyOn(services, "createInvestigation");

    renderForm();
    submit();

    expect(await screen.findByText(/server host is required/i)).toBeTruthy();
    expect(screen.getByText(/password is required/i)).toBeTruthy();
    expect(create).not.toHaveBeenCalled();
  });

  it("rejects an invalid host with an inline message", async () => {
    const create = vi.spyOn(services, "createInvestigation");

    renderForm();
    fireEvent.change(screen.getByLabelText(/server host/i), {
      target: { value: "not a host!" },
    });
    fireEvent.change(screen.getByLabelText(/password/i), {
      target: { value: "pw" },
    });
    submit();

    expect(
      await screen.findByText(/valid hostname or ip address/i),
    ).toBeTruthy();
    expect(create).not.toHaveBeenCalled();
  });

  it("offers only the API's lookback windows", () => {
    renderForm();

    const options = within(
      screen.getByLabelText(/lookback window/i),
    ).getAllByRole("option");
    expect(
      options.map((option) => (option as HTMLOptionElement).value),
    ).toEqual(LOOKBACK_HOURS.map(String));
  });

  it("rejects a lookback outside the API's set even when typed in", () => {
    const errors = validate({ ...validState, lookbackHours: 5 }, "pw");
    expect(errors.lookbackHours).toMatch(/1, 4, 8, or 24/i);
  });

  it("requires at least one database when scope is databases", async () => {
    const create = vi.spyOn(services, "createInvestigation");

    renderForm();
    fillRequired();
    fireEvent.click(
      screen.getByRole("radio", { name: /specific databases/i }),
    );
    submit();

    expect(await screen.findByText(/at least one database/i)).toBeTruthy();
    expect(create).not.toHaveBeenCalled();
  });

  it("submits exactly the typed payload, without the password, then navigates", async () => {
    const create = vi
      .spyOn(services, "createInvestigation")
      .mockResolvedValue({ id: 7, status: "draft", catalog: [] });

    renderForm();
    fillRequired();
    submit();

    await waitFor(() => {
      expect(create).toHaveBeenCalledWith({
        server_host: VALID_HOST,
        server_port: 1433,
        auth_type: "sql",
        databases: null,
        lookback_hours: 4,
      });
    });

    // The credential is never part of the request body.
    const payload = create.mock.calls[0][0];
    expect(payload).not.toHaveProperty("password");

    // Navigates to the approval screen carrying the returned id.
    expect(await screen.findByText(/approval screen/i)).toBeTruthy();
  });

  it("splits a comma-separated database scope", async () => {
    const create = vi
      .spyOn(services, "createInvestigation")
      .mockResolvedValue({ id: 8, status: "draft", catalog: [] });

    renderForm();
    fillRequired();
    fireEvent.click(screen.getByRole("radio", { name: /specific databases/i }));
    fireEvent.change(
      screen.getByLabelText(/databases \(comma-separated\)/i),
      { target: { value: " db1, db2 ,, " } },
    );
    submit();

    await waitFor(() => {
      expect(create).toHaveBeenCalledWith(
        expect.objectContaining({ databases: ["db1", "db2"] }),
      );
    });
  });

  it("maps a 422 response back onto the offending field", async () => {
    vi.spyOn(services, "createInvestigation").mockRejectedValue(
      new ApiError(422, "Validation failed", {
        detail: [{ loc: ["body", "server_host"], msg: "Field required" }],
      }),
    );

    renderForm();
    fillRequired();
    submit();

    expect(await screen.findByText(/field required/i)).toBeTruthy();
  });

  it("accepts Active Directory with a notice, without blocking submit", async () => {
    const create = vi
      .spyOn(services, "createInvestigation")
      .mockResolvedValue({ id: 9, status: "draft", catalog: [] });

    renderForm();
    fireEvent.change(screen.getByLabelText(/auth type/i), {
      target: { value: "ad" },
    });

    // Informational notice (points at the live-connection work), not a block.
    expect(await screen.findByText(/#31/)).toBeTruthy();
    expect(screen.queryByLabelText(/^password$/i)).toBeNull();

    fireEvent.change(screen.getByLabelText(/server host/i), {
      target: { value: VALID_HOST },
    });
    expect(
      screen.getByRole("button", { name: /create investigation/i }),
    ).toBeEnabled();
    submit();

    await waitFor(() => {
      expect(create).toHaveBeenCalledWith(
        expect.objectContaining({ auth_type: "ad" }),
      );
    });
  });
});
