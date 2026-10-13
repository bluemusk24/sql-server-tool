/**
 * Job status screen.
 *
 * Reached from the approval screen after starting a run. Polls
 * `GET /jobs/{job_id}` until the job reaches a terminal state, then
 * offers a link to the generated report. The current in-memory backend
 * finishes synchronously, so the first poll usually already shows
 * "succeeded" — the polling loop is here so the screen also works
 * against a real asynchronous backend.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { services } from "../services";
import type { JobState, JobStatusResponse } from "../services/types";

const TERMINAL: JobState[] = ["succeeded", "failed", "canceled"];
const POLL_MS = 1_000;

type State =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "running"; job: JobStatusResponse }
  | { status: "terminal"; job: JobStatusResponse };

export function JobStatusPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const [state, setState] = useState<State>({ status: "loading" });
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const poll = useCallback(async () => {
    if (!jobId) return;
    try {
      const job = await services.getJobStatus(jobId);
      if (TERMINAL.includes(job.status)) {
        setState({ status: "terminal", job });
        return; // Stop polling.
      }
      setState({ status: "running", job });
      timer.current = setTimeout(poll, POLL_MS);
    } catch (error) {
      setState({
        status: "error",
        message: error instanceof Error ? error.message : "could not load job",
      });
    }
  }, [jobId]);

  useEffect(() => {
    setState({ status: "loading" });
    void poll();
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [poll]);

  if (state.status === "loading") {
    return (
      <main className="job-status">
        <h1>Run status</h1>
        <p>Loading job…</p>
      </main>
    );
  }

  if (state.status === "error") {
    return (
      <main className="job-status">
        <h1>Run status</h1>
        <p role="alert">{state.message}</p>
        <p>
          <Link to="/">Back to investigations</Link>
        </p>
      </main>
    );
  }

  const { job } = state;
  const invId = job.investigation_id;

  return (
    <main className="job-status">
      <h1>Run status</h1>
      <p>
        Job <code>{job.job_id}</code>
      </p>
      <p>
        Status:{" "}
        <strong data-testid="job-state" data-state={job.status}>
          {job.status}
        </strong>
      </p>

      {state.status === "running" && <p>The run is in progress…</p>}

      {job.status === "succeeded" && invId !== null && (
        <p>
          <Link className="button" to={`/investigations/${invId}/report`}>
            View report
          </Link>
        </p>
      )}
      {job.status === "failed" && (
        <p role="alert">The run failed: {job.error ?? "unknown error"}</p>
      )}
      {job.status === "canceled" && <p>The run was canceled.</p>}

      <p>
        <Link to="/">Back to investigations</Link>
      </p>
    </main>
  );
}
