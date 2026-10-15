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
 * Each row also carries a Delete (confirmed) that removes the
 * investigation and its report from the queue (#39).
 */

import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { services } from "../services";
import type { InvestigationSummary } from "../services/types";
import { formatServerTarget } from "../lib/format";

type State =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ok"; items: InvestigationSummary[] };

/** Where the DBA should go next, given the investigation's status. */
function nextLink(id: number, status: string): { to: string; label: string } {
  // Canceled and timed-out runs keep their partial reports (#29).
  if (status === "completed" || status === "canceled" || status === "failed") {
    return { to: `/investigations/${id}/report`, label: "View report" };
  }
  return { to: `/investigations/${id}/approve`, label: "Review approvals" };
}

export function InvestigationList() {
  const [state, setState] = useState<State>({ status: "loading" });
  // Deletion failures keep the list on screen, so they get their own slot.
  const [deleteError, setDeleteError] = useState<string | null>(null);

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

  const onDelete = useCallback(async (inv: InvestigationSummary) => {
    const target = formatServerTarget(inv.server_host, inv.server_port);
    const confirmed = window.confirm(
      `Delete investigation #${inv.id} (${target})? ` +
        "Its report will be removed too.",
    );
    if (!confirmed) return;
    setDeleteError(null);
    try {
      await services.deleteInvestigation(inv.id);
      setState((prev) =>
        prev.status === "ok"
          ? { ...prev, items: prev.items.filter((x) => x.id !== inv.id) }
          : prev,
      );
    } catch (error) {
      setDeleteError(
        error instanceof Error ? error.message : "could not delete",
      );
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
      {deleteError && <p role="alert">{deleteError}</p>}

      {state.status === "ok" && (
        <>
          {state.items.length === 0 ? (
            <div className="empty">
              <div className="empty-glyph" aria-hidden="true">
                ◧
              </div>
              <p>No investigations yet.</p>
              <p className="hint">
                Walk the flow: create → approve → run → report.
              </p>
              <Link className="button" to="/investigations/new">
                Create your first investigation
              </Link>
            </div>
          ) : (
            <ul>
              {state.items.map((inv) => {
                const link = nextLink(inv.id, inv.status);
                return (
                  <li key={inv.id}>
                    <span className="row-info">
                      #{inv.id} —{" "}
                      <strong className="host">
                        {formatServerTarget(inv.server_host, inv.server_port)}
                      </strong>{" "}
                      <strong data-state={inv.status}>{inv.status}</strong>
                    </span>
                    <span className="row-actions">
                      <Link to={link.to}>{link.label}</Link>
                      <button
                        type="button"
                        className="btn-danger-ghost btn-sm"
                        onClick={() => void onDelete(inv)}
                      >
                        Delete
                      </button>
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
          <button className="btn-secondary btn-sm" onClick={() => void load()}>
            Refresh
          </button>
        </>
      )}
    </section>
  );
}
