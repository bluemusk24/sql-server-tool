/**
 * Fleet view (#50): failure summary across the organization's servers.
 *
 * The multi-firm analysis picture — which of *our* SQL Servers are
 * failing, in which issue areas, and whether the database refresh path
 * is among them — aggregated by the backend from stored reports and
 * scoped to the signed-in DBA's organization (spec §6: no
 * cross-organization visibility, ever).
 */

import { useEffect, useState } from "react";
import type { FC } from "react";
import { Link } from "react-router-dom";
import { services } from "../services";
import type { FleetSummary } from "../services/types";
import { areaTitle } from "../lib/areas";

export const FleetPage: FC = () => {
  const [fleet, setFleet] = useState<FleetSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    services
      .getFleet()
      .then(setFleet)
      .catch((err: unknown) =>
        setError(err instanceof Error ? err.message : "fetch error"),
      );
  }, []);

  if (error) {
    return (
      <main className="report fleet">
        <p role="alert">{error}</p>
      </main>
    );
  }
  if (!fleet) {
    return (
      <main className="report fleet">
        <p className="hint">Loading fleet health…</p>
      </main>
    );
  }

  const empty = fleet.totals.servers === 0;

  return (
    <main className="report fleet" data-testid="fleet-page">
      <h1>Fleet health — {fleet.organization}</h1>
      <p className="muted">
        Failures across every server this organization has investigated,
        aggregated from stored reports (7-day window). Analysis only — the
        tool never changes anything on these servers.
      </p>

      <section aria-label="Totals">
        <h2>Totals</h2>
        <p>
          <span className="chip">{fleet.totals.servers} server(s)</span>{" "}
          <span className="chip">
            {fleet.totals.investigations} investigation(s)
          </span>{" "}
          <span className="chip" data-state="high">
            {fleet.totals.findings.high} high
          </span>{" "}
          <span className="chip" data-state="medium">
            {fleet.totals.findings.medium} medium
          </span>{" "}
          <span className="chip" data-state="low">
            {fleet.totals.findings.low} low
          </span>{" "}
          <span
            className="chip"
            data-state={fleet.totals.refresh_findings > 0 ? "high" : "low"}
          >
            {fleet.totals.refresh_findings} refresh finding(s)
          </span>
        </p>
      </section>

      {empty ? (
        <section aria-label="Empty state">
          <p className="hint">
            No investigations yet for {fleet.organization} — run one to build
            your fleet picture.
          </p>
        </section>
      ) : (
        <>
          <section aria-label="Servers">
            <h2>Servers</h2>
            <ul>
              {fleet.servers.map((server) => (
                <li key={`${server.server_host}:${server.server_port}`}>
                  <span className="row-info">
                    <strong className="host">
                      {server.server_host}:{server.server_port}
                    </strong>{" "}
                    <span className="chip">
                      {server.investigations} investigation(s)
                    </span>{" "}
                    <span className="chip" data-state="high">
                      {server.findings_by_severity.high} high
                    </span>{" "}
                    <span className="chip" data-state="medium">
                      {server.findings_by_severity.medium} medium
                    </span>{" "}
                    <span className="chip" data-state="low">
                      {server.findings_by_severity.low} low
                    </span>{" "}
                    <span
                      className="chip"
                      data-state={server.refresh_findings > 0 ? "high" : "low"}
                      title="Database refresh & replication findings"
                    >
                      refresh: {server.refresh_findings}
                    </span>
                    {Object.entries(server.areas).map(([area, bucket]) => (
                      <span
                        key={area}
                        className="chip"
                        data-state={bucket.severity}
                        title={`${bucket.findings} finding(s)`}
                      >
                        {areaTitle(area)}: {bucket.findings}
                      </span>
                    ))}
                    {server.inconclusive && (
                      <span className="chip" data-state="low">
                        inconclusive
                      </span>
                    )}
                  </span>
                  <span className="row-actions">
                    {server.latest_report_id !== null ? (
                      <Link
                        to={`/investigations/${server.latest_report_id}/report`}
                      >
                        {server.latest_primary_cause} (
                        {server.latest_confidence_percent}%)
                      </Link>
                    ) : (
                      <span className="muted">
                        {server.latest_primary_cause || "no report yet"}
                      </span>
                    )}
                    <span className="muted">
                      last:{" "}
                      {server.last_investigation
                        ? new Date(server.last_investigation).toLocaleString()
                        : "—"}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          </section>

          <section aria-label="Recent failures">
            <h2>Recent failures</h2>
            {fleet.recent_failures.length === 0 ? (
              <p className="hint">No high or medium failures in the window.</p>
            ) : (
              <ul>
                {fleet.recent_failures.map((failure, index) => (
                  <li key={`${failure.investigation_id}-${index}`}>
                    <span className="row-info">
                      <span className="chip" data-state={failure.severity}>
                        {failure.severity}
                      </span>{" "}
                      <Link to={`/investigations/${failure.investigation_id}/report`}>
                        {failure.title}
                      </Link>{" "}
                      <span className="muted">
                        — {failure.server_host}:{failure.server_port}
                        {failure.database ? ` · ${failure.database}` : ""} ·{" "}
                        {areaTitle(failure.area)}
                      </span>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </main>
  );
};
