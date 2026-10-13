/**
 * Connection & Scope form (#23).
 *
 * Collects the DBA's SQL Server connection details, authentication type,
 * scope, and lookback window, then POSTs to `POST /investigations` and
 * navigates to the approval screen.
 *
 * Validation mirrors the backend's `CreateInvestigationRequest` exactly —
 * host non-empty and a valid hostname/IP, port 1–65535, a non-empty database
 * list when scope is `databases`, lookback in {1,4,8,24}, and a password when
 * auth type is `sql` — and runs inline before the request is sent. A 422 from
 * the API is mapped back onto the offending field when the response names one.
 *
 * Credentials: the password lives in isolated state, is never part of the
 * payload, and is cleared on submit and on unmount (#23). Wiring it to a real
 * connection is tracked in #31.
 */

import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, LOOKBACK_HOURS, getServiceClient } from "../services";
import type { LookbackHours } from "../services";

export type AuthType = "sql" | "ad";
export type Scope = "instance" | "databases";

/** Connection/scope/lookback state held in one object (#23 constraint). */
export interface FormState {
  serverHost: string;
  serverPort: string;
  authType: AuthType;
  scope: Scope;
  databases: string;
  lookbackHours: number;
}

export type FieldKey =
  | "serverHost"
  | "serverPort"
  | "databases"
  | "lookbackHours"
  | "password";

export type FieldErrors = Partial<Record<FieldKey, string>>;

const INITIAL: FormState = {
  serverHost: "",
  serverPort: "1433",
  authType: "sql",
  scope: "instance",
  databases: "",
  lookbackHours: 4,
};

const IPV4_RE =
  /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/;
const LABEL_RE = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$/;

/** True for a non-empty hostname (`sql01`, `sql01.acme.example`), IPv4, or IPv6. */
export function isValidHost(value: string): boolean {
  const host = value.trim();
  if (host.length === 0 || host.length > 253) return false;
  if (IPV4_RE.test(host)) return true;
  if (host.includes(":")) return /^[0-9a-fA-F:]+$/.test(host); // IPv6
  return host
    .replace(/\.$/, "")
    .split(".")
    .every((label) => LABEL_RE.test(label));
}

/** `"db1, db2"` → `["db1", "db2"]`; blanks and whitespace are dropped. */
export function parseDatabases(value: string): string[] {
  return value
    .split(",")
    .map((db) => db.trim())
    .filter(Boolean);
}

function isLookback(value: number): value is LookbackHours {
  return (LOOKBACK_HOURS as readonly number[]).includes(value);
}

/** Validate the form; mirrors `CreateInvestigationRequest` in the backend. */
export function validate(state: FormState, password: string): FieldErrors {
  const errors: FieldErrors = {};

  if (state.serverHost.trim() === "") {
    errors.serverHost = "Server host is required.";
  } else if (!isValidHost(state.serverHost)) {
    errors.serverHost = "Enter a valid hostname or IP address.";
  }

  const port = Number(state.serverPort);
  if (
    state.serverPort.trim() === "" ||
    !Number.isInteger(port) ||
    port < 1 ||
    port > 65535
  ) {
    errors.serverPort = "Port must be a whole number from 1 to 65535.";
  }

  if (state.scope === "databases" && parseDatabases(state.databases).length === 0) {
    errors.databases = "List at least one database, or choose Whole instance.";
  }

  if (!isLookback(state.lookbackHours)) {
    errors.lookbackHours = "Choose a lookback of 1, 4, 8, or 24 hours.";
  }

  if (state.authType === "sql" && password === "") {
    errors.password = "Password is required for SQL Server authentication.";
  }

  return errors;
}

/** API field name → local field key, for mapping a 422 back to a control. */
const FIELD_BY_API_NAME: Record<string, FieldKey> = {
  server_host: "serverHost",
  server_port: "serverPort",
  databases: "databases",
  lookback_hours: "lookbackHours",
};

/** Turn an `ApiError` into a message, plus the offending field when known. */
export function mapApiError(error: unknown): { field?: FieldKey; message: string } {
  if (error instanceof ApiError && error.status === 422) {
    const detail = (error.body as { detail?: unknown } | undefined)?.detail;
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { loc?: unknown; msg?: unknown };
      const loc = Array.isArray(first.loc) ? first.loc : [];
      const apiField = typeof loc[1] === "string" ? loc[1] : "";
      const message = typeof first.msg === "string" ? first.msg : "Invalid value.";
      const field = FIELD_BY_API_NAME[apiField];
      return field ? { field, message } : { message };
    }
    if (typeof detail === "string") return { message: detail };
  }
  return {
    message: error instanceof Error ? error.message : "Failed to create investigation",
  };
}

export function ConnectionScopeForm() {
  const navigate = useNavigate();
  const [form, setForm] = useState<FormState>(INITIAL);
  // Isolated on purpose: never merged into `form`, so it cannot be logged,
  // persisted, or stringified alongside the other fields.
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState<FieldErrors>({});
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Never leave a credential behind when the screen goes away.
  useEffect(() => () => setPassword(""), []);

  const setField = <K extends keyof FormState>(key: K, value: FormState[K]) => {
    const next: FormState = { ...form, [key]: value };
    setForm(next);
    // Re-validate a field that is already showing an error, so its message
    // clears as soon as the DBA fixes it.
    if (errors[key as FieldKey]) setErrors(validate(next, password));
  };

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();

    const found = validate(form, password);
    setErrors(found);
    if (Object.keys(found).length > 0) return; // show inline errors, send nothing

    setSubmitting(true);
    setSubmitError(null);
    try {
      const created = await getServiceClient().createInvestigation({
        server_host: form.serverHost.trim(),
        server_port: Number(form.serverPort),
        auth_type: form.authType,
        databases:
          form.scope === "databases" ? parseDatabases(form.databases) : null,
        lookback_hours: form.lookbackHours as LookbackHours,
      });
      setPassword(""); // drop the credential the moment it is no longer needed
      navigate(`/investigations/${created.id}/approve`);
    } catch (e) {
      const mapped = mapApiError(e);
      if (mapped.field) {
        setErrors((prev) => ({ ...prev, [mapped.field as FieldKey]: mapped.message }));
      } else {
        setSubmitError(mapped.message);
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="connection-form">
      <h1>New Investigation</h1>
      {submitError && <p role="alert">{submitError}</p>}
      <form onSubmit={onSubmit} noValidate>
        <label htmlFor="server-host">
          Server host
          <input
            id="server-host"
            type="text"
            value={form.serverHost}
            onChange={(e) => setField("serverHost", e.target.value)}
            aria-invalid={errors.serverHost ? true : undefined}
            aria-describedby={errors.serverHost ? "server-host-error" : undefined}
            autoComplete="off"
            required
          />
        </label>
        {errors.serverHost && (
          <p role="alert" id="server-host-error">
            {errors.serverHost}
          </p>
        )}

        <label htmlFor="server-port">
          Port
          <input
            id="server-port"
            type="number"
            value={form.serverPort}
            onChange={(e) => setField("serverPort", e.target.value)}
            min={1}
            max={65535}
            aria-invalid={errors.serverPort ? true : undefined}
            aria-describedby={errors.serverPort ? "server-port-error" : undefined}
            required
          />
        </label>
        {errors.serverPort && (
          <p role="alert" id="server-port-error">
            {errors.serverPort}
          </p>
        )}

        <label htmlFor="auth-type">
          Auth type
          <select
            id="auth-type"
            value={form.authType}
            onChange={(e) => setField("authType", e.target.value as AuthType)}
            aria-describedby={form.authType === "ad" ? "auth-type-note" : undefined}
          >
            <option value="sql">SQL Server</option>
            <option value="ad">Active Directory</option>
          </select>
        </label>
        {form.authType === "ad" && (
          <p className="muted" id="auth-type-note">
            Active Directory is accepted here. AD/Kerberos credentials are used when
            the run connects to the server — that work is tracked in #31.
          </p>
        )}

        {form.authType === "sql" && (
          <>
            <label htmlFor="password">
              Password
              <input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                aria-invalid={errors.password ? true : undefined}
                aria-describedby={
                  errors.password ? "password-error" : "password-help"
                }
                autoComplete="off"
                required
              />
            </label>
            <p className="muted" id="password-help">
              Session-only: never stored in the investigation, report, or logs.
            </p>
            {errors.password && (
              <p role="alert" id="password-error">
                {errors.password}
              </p>
            )}
          </>
        )}

        <fieldset>
          <legend>Scope</legend>
          <label>
            <input
              type="radio"
              name="scope"
              value="instance"
              checked={form.scope === "instance"}
              onChange={() => setField("scope", "instance")}
            />
            Whole instance
          </label>
          <label>
            <input
              type="radio"
              name="scope"
              value="databases"
              checked={form.scope === "databases"}
              onChange={() => setField("scope", "databases")}
            />
            Specific databases
          </label>
        </fieldset>
        {form.scope === "databases" && (
          <>
            <label htmlFor="databases">
              Databases (comma-separated)
              <input
                id="databases"
                type="text"
                value={form.databases}
                onChange={(e) => setField("databases", e.target.value)}
                placeholder="db1, db2, db3"
                aria-invalid={errors.databases ? true : undefined}
                aria-describedby={errors.databases ? "databases-error" : undefined}
              />
            </label>
            {errors.databases && (
              <p role="alert" id="databases-error">
                {errors.databases}
              </p>
            )}
          </>
        )}

        <label htmlFor="lookback">
          Lookback window
          <select
            id="lookback"
            value={form.lookbackHours}
            onChange={(e) => setField("lookbackHours", Number(e.target.value))}
            aria-invalid={errors.lookbackHours ? true : undefined}
            aria-describedby={errors.lookbackHours ? "lookback-error" : undefined}
          >
            {LOOKBACK_HOURS.map((hours) => (
              <option key={hours} value={hours}>
                {hours} hours
              </option>
            ))}
          </select>
        </label>
        {errors.lookbackHours && (
          <p role="alert" id="lookback-error">
            {errors.lookbackHours}
          </p>
        )}

        <button type="submit" disabled={submitting}>
          {submitting ? "Creating…" : "Create Investigation"}
        </button>
      </form>
    </main>
  );
}
