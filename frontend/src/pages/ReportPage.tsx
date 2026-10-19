/**
 * Report dashboard.
 *
 * Renders the diagnostic report returned by `GET /reports/{id}` and
 * offers a PDF download (`GET /reports/{id}.pdf`). This is the payoff
 * screen: after a run succeeds, the DBA lands here to see the summary,
 * per-source collection status, primary cause, findings by area with
 * their raw evidence, coverage gaps, validation checks, and read-only
 * remediation guidance (#25).
 */

import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, services } from "../services";
import type { ReportPayload } from "../services/types";
import { formatServerTarget } from "../lib/format";
import { areaTitle } from "../lib/areas";

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

      {r.synthetic && (
        <p className="synthetic-banner" role="note" data-testid="synthetic-banner">
          Synthetic sample data — not collected from a real server.
        </p>
      )}

      <section>
        <h2>Summary</h2>
        <dl>
          <dt>Server</dt>
          <dd>
            {formatServerTarget(r.server_host, r.server_port)}
          </dd>
          <dt>Scope</dt>
          <dd>{r.database_scope}</dd>
          <dt>Lookback</dt>
          <dd>{r.lookback_hours} hours</dd>
          <dt>Severity</dt>
          <dd data-testid="report-severity" data-state={r.severity}>
            {r.severity}
          </dd>
          <dt>Confidence</dt>
          <dd>
            {r.confidence_percent}%{" "}
            <span className="chip">({r.confidence_label})</span>
          </dd>
        </dl>
      </section>

      {r.data_sources.length > 0 && (
        <section>
          <h2>Data sources</h2>
          <ul>
            {r.data_sources.map((source) => (
              <li key={source.source_id}>
                <strong>{source.title}</strong>{" "}
                <em data-testid={`source-status-${source.source_id}`}>
                  ({source.status}
                  {source.checked ? "" : ", not checked"})
                </em>
                {source.note && <> — {source.note}</>}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="primary-cause">
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
              {areaTitle(area.area)}{" "}
              <span className="chip" data-state={area.severity}>
                (severity: {area.severity})
              </span>
            </h3>
            <p>
              Confidence {area.confidence_percent}% ({area.confidence_label}),{" "}
              {area.signal_count} signal(s)
            </p>
            {area.findings.length > 0 && (
              <ul>
                {area.findings.map((signal) => (
                  <li key={signal.rule_id}>
                    <strong>{signal.title}</strong>{" "}
                    <span className="chip" data-state={signal.severity}>
                      (severity: {signal.severity})
                    </span>{" "}
                    {signal.database && (
                      <span className="chip" data-state="ok" title="Affected database">
                        db: {signal.database}
                      </span>
                    )}{" "}
                    {signal.kind && (
                      <span className="chip" title="Engine rule kind">
                        {signal.kind}
                      </span>
                    )}{" "}
                    — {signal.description}
                  </li>
                ))}
              </ul>
            )}
            {area.evidence.length > 0 && (
              <details>
                <summary>Raw evidence ({area.evidence.length})</summary>
                <ul>
                  {area.evidence.map((item) => (
                    <li key={item.evidence_id}>
                      <p>{item.description}</p>
                      <p className="muted">
                        {item.evidence_id}
                        {item.timestamp ? ` · ${item.timestamp}` : ""}
                      </p>
                      {Object.keys(item.details).length > 0 && (
                        <pre>{JSON.stringify(item.details, null, 2)}</pre>
                      )}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        ))}
      </section>

      {r.coverage_gaps.length > 0 && (
        <section>
          <h2>Coverage gaps</h2>
          <ul>
            {r.coverage_gaps.map((gap) => (
              <li key={`${gap.source_id}-${gap.issue_area}`}>
                <strong>{gap.issue_area}</strong> ({gap.status}) — {gap.note}
              </li>
            ))}
          </ul>
        </section>
      )}

      {r.validation_checks.length > 0 && (
        <section>
          <h2>Validation checks</h2>
          <ul>
            {r.validation_checks.map((check, i) => (
              <li key={i}>{check}</li>
            ))}
          </ul>
        </section>
      )}

      {r.recommendations.length > 0 && (
        <section>
          <h2>Recommendations</h2>
          {r.recommendations.map((rec, i) => (
            <div key={i}>
              {rec.validation_checks.length > 0 && (
                <>
                  <p>
                    <strong>Validate first:</strong>
                  </p>
                  <ul>
                    {rec.validation_checks.map((check, k) => (
                      <li key={k}>{check}</li>
                    ))}
                  </ul>
                </>
              )}
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
        <button
          className="btn-secondary"
          onClick={onDownload}
          disabled={downloading}
        >
          {downloading ? "Preparing…" : "Download PDF"}
        </button>
      </section>

      <p>
        <Link to="/">Back to investigations</Link>
      </p>
    </main>
  );
}
