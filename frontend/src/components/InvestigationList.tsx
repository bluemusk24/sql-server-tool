/**
 * Investigation list.
 *
 * Shows every investigation in the shared backend store — naming the
 * SQL Server it targets (#34) — with a link to the right next screen
 * based on status:
 *   - draft / pending_approval → approval screen
 *   - completed → report
 * A "refresh" button re-fetches, which is how a second browser window
 * picks up investigations created elsewhere (there is no live push).
 */

import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { services } from "../services";
import type { InvestigationSummary } from "../services/types";

type State =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ok"; items: InvestigationSummary[] };

/** Where the DBA should go next, given the investigation's status. */
function nextLink(id: number, status: string): { to: string; label: string } {
  if (status === "completed") {
    return { to: `/investigations/${id}/report`, label: "View report" };
  }
  return { to: `/investigations/${id}/approve`, label: "Review approvals" };
}

export function InvestigationList() {
  const [state, setState] = useState<State>({ status: "loading" });

  const load = useCallback(async () => {
    setState({ status: "loading" });
    try {
      const items = await services.listInvestigations();
      setState({ status: "ok", items });
    } catch (error) {
      setState({
        status: "error",
        message: error instanceof Error ? error.message : "could not load",
      });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section className="investigation-list">
      <h2>Investigations</h2>

      {state.status === "loading" && <p>Loading…</p>}
      {state.status === "error" && (
        <p role="alert">{state.message}</p>
      )}

      {state.status === "ok" && (
        <>
          {state.items.length === 0 ? (
            <p>No investigations yet.</p>
          ) : (
            <ul>
              {state.items.map((inv) => {
                const link = nextLink(inv.id, inv.status);
                return (
                  <li key={inv.id}>
                    <span>
                      #{inv.id} —{" "}
                      <strong className="host">
                        {inv.server_host}:{inv.server_port}
                      </strong>{" "}
                      <strong data-state={inv.status}>{inv.status}</strong>
                    </span>{" "}
                    <Link to={link.to}>{link.label}</Link>
                  </li>
                );
              })}
            </ul>
          )}
          <button onClick={() => void load()}>Refresh</button>
        </>
      )}
    </section>
  );
}
