/**
 * The authenticated landing page.
 *
 * A hub: shows who you are, API health, a "New investigation" action,
 * and the list of investigations in the shared backend store. This is
 * also the screen a second browser window lands on after signing in,
 * so it's where cross-window changes become visible (via Refresh).
 */

import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { HealthStatus } from "../components/HealthStatus";
import { InvestigationList } from "../components/InvestigationList";

export function LandingPage() {
  const { state, signOut } = useAuth();
  const session = state.status === "authenticated" ? state.session : null;

  return (
    <main className="landing">
      <section className="hero">
        <h1>SQL Server Diagnostic Assistant</h1>
        <p className="hero-sub">
          Read-only diagnostics for any SQL Server — collect evidence, approve
          the run, and hand the DBA a report.
        </p>
        <p className="org">Organization: {session?.organization}</p>
        <p className="actor">
          Signed in as {session?.actor}{" "}
          <button onClick={signOut}>Sign out</button>
        </p>
        <div className="hero-actions">
          <Link className="button" to="/investigations/new">
            New investigation
          </Link>
          <HealthStatus />
        </div>
      </section>

      <InvestigationList />
    </main>
  );
}
