/**
 * Job status screen.
 *
 * Reached from the approval screen after starting a run. Polls
 * `GET /jobs/{job_id}` until the job reaches a terminal state, then
 * offers a link to the generated report. Runs execute on a backend
 * worker thread (#29), so this screen is where the DBA watches — and
 * can cancel — a run in flight. A canceled or failed run keeps the
 * evidence it managed to collect, so its report link reads "partial".
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
  const [canceling, setCanceling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);
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

  const onCancel = useCallback(async () => {
    if (!jobId) return;
    setCanceling(true);
    setCancelError(null);
    try {
      await services.cancelJob(jobId);
      await poll(); // pick up the canceled state right away
    } catch (error) {
      setCancelError(
        error instanceof Error ? error.message : "could not cancel the run",
      );
    } finally {
      setCanceling(false);
    }
  }, [jobId, poll]);

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

      {state.status === "running" && (
        <p>
          The run is in progress…{" "}
          <button
            type="button"
            onClick={() => void onCancel()}
            disabled={canceling}
          >
            {canceling ? "Canceling…" : "Cancel run"}
          </button>
        </p>
      )}
      {cancelError && <p role="alert">{cancelError}</p>}

      {job.status === "succeeded" && invId !== null && (
        <p>
          <Link className="button" to={`/investigations/${invId}/report`}>
            View report
          </Link>
        </p>
      )}
      {job.status === "failed" && (
        <p role="alert">
          The run failed: {job.error ?? "unknown error"}
          {invId !== null && (
            <>
              {" "}
              <Link to={`/investigations/${invId}/report`}>
                View partial report
              </Link>
            </>
          )}
        </p>
      )}
      {job.status === "canceled" && (
        <p>
          The run was canceled.
          {invId !== null && (
            <>
              {" "}
              <Link to={`/investigations/${invId}/report`}>
                View partial report
              </Link>
            </>
          )}
        </p>
      )}

      <p>
        <Link to="/">Back to investigations</Link>
      </p>
    </main>
  );
}
