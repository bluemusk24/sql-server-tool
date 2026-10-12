/**
 * Connection & Scope form (#23).
 *
 * Collects the DBA's SQL Server connection details, authentication
 * type, scope, and lookback window, then POSTs to the investigations
 * endpoint and navigates to the approval screen.
 *
 * This is a minimal scaffold — full validation and submission will
 * be wired once the services layer migration is complete.
 */

import { useState } from "react";
import type { FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { getServiceClient } from "../services";

const LOOKBACK_HOURS = [1, 4, 8, 24] as const;

export function ConnectionScopeForm() {
  const navigate = useNavigate();
  const [serverHost, setServerHost] = useState("");
  const [serverPort, setServerPort] = useState("1433");
  const [authType, setAuthType] = useState<"sql" | "ad">("sql");
  const [password, setPassword] = useState("");
  const [scope, setScope] = useState<"instance" | "databases">("instance");
  const [databases, setDatabases] = useState("");
  const [lookbackHours, setLookbackHours] = useState<number>(4);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const services = getServiceClient();
      const created = await services.createInvestigation({
        server_host: serverHost,
        server_port: Number(serverPort),
        auth_type: authType,
        databases: scope === "databases" ? databases.split(",").map((d) => d.trim()).filter(Boolean) : null,
        lookback_hours: lookbackHours as 1 | 4 | 8 | 24,
      });
      // Clear password from state after submit
      setPassword("");
      navigate(`/investigations/${created.id}/approve`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create investigation");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="connection-form">
      <h1>New Investigation</h1>
      {error && <p role="alert">{error}</p>}
      <form onSubmit={onSubmit}>
        <label>
          Server host
          <input
            type="text"
            value={serverHost}
            onChange={(e) => setServerHost(e.target.value)}
            required
          />
        </label>
        <label>
          Port
          <input
            type="number"
            value={serverPort}
            onChange={(e) => setServerPort(e.target.value)}
            min={1}
            max={65535}
          />
        </label>
        <label>
          Auth type
          <select value={authType} onChange={(e) => setAuthType(e.target.value as "sql" | "ad")}>
            <option value="sql">SQL Server</option>
            <option value="ad">Active Directory</option>
          </select>
        </label>
        {authType === "sql" && (
          <label>
            Password
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </label>
        )}
        <fieldset>
          <legend>Scope</legend>
          <label>
            <input
              type="radio"
              name="scope"
              value="instance"
              checked={scope === "instance"}
              onChange={() => setScope("instance")}
            />
            Whole instance
          </label>
          <label>
            <input
              type="radio"
              name="scope"
              value="databases"
              checked={scope === "databases"}
              onChange={() => setScope("databases")}
            />
            Specific databases
          </label>
        </fieldset>
        {scope === "databases" && (
          <label>
            Databases (comma-separated)
            <input
              type="text"
              value={databases}
              onChange={(e) => setDatabases(e.target.value)}
              placeholder="db1, db2, db3"
            />
          </label>
        )}
        <label>
          Lookback window
          <select value={lookbackHours} onChange={(e) => setLookbackHours(Number(e.target.value))}>
            {LOOKBACK_HOURS.map((h) => (
              <option key={h} value={h}>{h} hours</option>
            ))}
          </select>
        </label>
        <button type="submit" disabled={submitting}>
          {submitting ? "Creating…" : "Create Investigation"}
        </button>
      </form>
    </main>
  );
}
