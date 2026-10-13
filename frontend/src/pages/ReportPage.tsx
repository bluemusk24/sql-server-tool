/**
 * Report dashboard.
 *
 * Renders the diagnostic report returned by `GET /reports/{id}` and
 * offers a PDF download (`GET /reports/{id}.pdf`). This is the payoff
 * screen: after a run succeeds, the DBA lands here to see the primary
 * cause, per-area findings, and read-only remediation guidance.
 */

import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, services } from "../services";
import type { ReportPayload } from "../services/types";

type State =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ok"; report: ReportPayload };

export function ReportPage() {
  const { id } = useParams<{ id: string }>();
  const [state, setState] = useState<State>({ status: "loading" });
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    services
      .getReportPayload(Number(id))
      .then((report) => {
        if (!cancelled) setState({ status: "ok", report });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        const message =
          error instanceof ApiError && error.status === 404
            ? "No report for this investigation yet — run it first."
            : error instanceof Error
              ? error.message
              : "could not load the report";
        setState({ status: "error", message });
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  const onDownload = async () => {
    if (!id) return;
    setDownloading(true);
    try {
      const blob = await services.downloadReportPdf(Number(id));
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `investigation-${id}.pdf`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } finally {
      setDownloading(false);
    }
  };

  if (state.status === "loading") {
    return (
      <main className="report">
        <h1>Diagnostic report</h1>
        <p>Loading report…</p>
      </main>
    );
  }

  if (state.status === "error") {
    return (
      <main className="report">
        <h1>Diagnostic report</h1>
        <p role="alert">{state.message}</p>
        <p>
          <Link to="/">Back to investigations</Link>
        </p>
      </main>
    );
  }

  const r = state.report;

  return (
    <main className="report">
      <h1>Diagnostic report</h1>
      <p className="muted">
        Investigation #{r.investigation_id} · {r.organization}
      </p>

      <section>
        <h2>Summary</h2>
        <dl>
          <dt>Server</dt>
          <dd>
            {r.server_host}:{r.server_port}
          </dd>
          <dt>Scope</dt>
          <dd>{r.database_scope}</dd>
          <dt>Lookback</dt>
          <dd>{r.lookback_hours} hours</dd>
          <dt>Severity</dt>
          <dd data-testid="report-severity">{r.severity}</dd>
          <dt>Confidence</dt>
          <dd>
            {r.confidence_percent}% ({r.confidence_label})
          </dd>
        </dl>
      </section>

      <section>
        <h2>Primary cause</h2>
        <p>
          <strong>{r.primary_cause.title}</strong>{" "}
          ({r.primary_cause.confidence_percent}% confidence)
        </p>
        <p>{r.primary_cause.description}</p>
      </section>

      {r.alternatives.length > 0 && (
        <section>
          <h2>Alternative causes</h2>
          <ul>
            {r.alternatives.map((alt) => (
              <li key={alt.title}>
                <strong>{alt.title}</strong> — {alt.description}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section>
        <h2>Findings by area</h2>
        {r.findings_by_area.map((area) => (
          <div key={area.area}>
            <h3>
              {area.area} <em>(severity: {area.severity})</em>
            </h3>
            <p>
              Confidence {area.confidence_percent}% ({area.confidence_label}),{" "}
              {area.signal_count} signal(s)
            </p>
          </div>
        ))}
      </section>

      {r.recommendations.length > 0 && (
        <section>
          <h2>Recommendations</h2>
          {r.recommendations.map((rec, i) => (
            <div key={i}>
              <p>{rec.disclaimer}</p>
              <ol>
                {rec.remediation.map((step, j) => (
                  <li key={j}>{step.step}</li>
                ))}
              </ol>
            </div>
          ))}
        </section>
      )}

      <section>
        <p className="disclaimer">{r.read_only_disclaimer}</p>
        <button onClick={onDownload} disabled={downloading}>
          {downloading ? "Preparing…" : "Download PDF"}
        </button>
      </section>

      <p>
        <Link to="/">Back to investigations</Link>
      </p>
    </main>
  );
}
