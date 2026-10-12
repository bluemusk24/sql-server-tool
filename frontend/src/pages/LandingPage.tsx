/**
 * The authenticated landing page (#22).
 *
 * Renders the caller's organization and the server shell the
 * investigation form (#23) will fill in, plus the API health
 * indicator. This is intentionally bare: it is the shell the
 * later screens grow into.
 */

import { HealthStatus } from "../components/HealthStatus";
import type { Session } from "../api/types";

export function LandingPage({ session }: { session: Session }) {
  return (
    <main className="landing">
      <h1>SQL Server Diagnostic Assistant</h1>
      <p className="org">Organization: {session.organization}</p>
      <p className="actor">Signed in as {session.actor}</p>
      <HealthStatus />
      <section className="server-shell">
        <h2>Server</h2>
        <p>Connection, scope, and lookback setup arrive with the form.</p>
      </section>
    </main>
  );
}
