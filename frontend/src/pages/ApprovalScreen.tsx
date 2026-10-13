import { useEffect, useState, useMemo } from "react";
import { useNavigate, useParams } from "react-router-dom";
import type { FC } from "react";
import { ApiError, clearCredentials, peekCredentials, services } from "../services";
import type {
  ApprovalResponse,
  InvestigationResponse,
} from "../services/types";
import { QuerySql } from "../components/QuerySql";

interface DecisionMap {
  [catalogId: string]: "approved" | "rejected" | "pending";
}

export const ApprovalScreen: FC = () => {
  const { id: investigationId } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [investigation, setInvestigation] = useState<InvestigationResponse | null>(null);
  const [approvalResponse, setApprovalResponse] = useState<ApprovalResponse>({
    investigation_id: 0,
    state: "pending",
    queries: [],
  });
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState<boolean>(false);
  const [readonly, setReadonly] = useState<boolean>(false);

  const [decisions, setDecisions] = useState<DecisionMap>({});
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [noteErrors, setNoteErrors] = useState<Record<string, string>>({});

  const totalCount = useMemo(
    () => (investigation ? investigation.catalog.length : 0),
    [investigation],
  );
  const approvedCount = approvalResponse?.queries?.filter((q) => q.decision === "approved").length ?? 0;
  // pendingCount is derived for potential future use; suppress unused warning
  const _pendingCount = approvalResponse?.queries?.filter((q) => q.decision === null).length ?? 0;
  void _pendingCount;
  const rejectedQuery = approvalResponse?.queries?.find((q) => q.decision === "rejected") || null;
  const allApproved = approvedCount === totalCount && totalCount > 0;

  useEffect(() => {
    if (!investigationId) return;
    services
      .getInvestigation(parseInt(investigationId))
      .then(setInvestigation)
      .catch((err) => {
        setError(err instanceof Error ? err.message : "fetch error");
      });
  }, [investigationId]);

  const handleDecision = async (
    catalogId: string,
    decision: "approved" | "rejected",
    note: string | null,
  ) => {
    if (!investigationId) return;
    try {
      setError(null);
      const payload = {
        decisions: { [catalogId]: decision },
        note: decision === "rejected" ? note || null : null,
      };
      const resp = await services.recordDecisions(parseInt(investigationId), payload);
      setApprovalResponse(resp);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setReadonly(true);
        setError("Run already started or cancelled – screen is read‑only");
      } else {
        setError(e instanceof Error ? e.message : "submit error");
        // The server didn't record the decision — revert the local choice
        // so the control doesn't show an approval that never happened.
        setDecisions((prev) => {
          const copy = { ...prev };
          delete copy[catalogId];
          return copy;
        });
      }
    }
  };

  // Trigger POST for rejected decisions when a valid note is entered
  useEffect(() => {
    if (!investigationId) return;
    Object.entries(decisions).forEach(([catalogId, decision]) => {
      if (decision === "rejected") {
        const note = notes[catalogId] || "";
        if (note.length >= 10) {
          // send if not already posted (we send on note change)
          const alreadyReplied = approvalResponse?.queries
            .find((q) => q.catalog_id === catalogId)
            ?.decision === "rejected";
          if (!alreadyReplied) {
            handleDecision(catalogId, decision, note);
          }
        }
      }
    });
  }, [notes, decisions, approvalResponse, investigationId]);

  const onApproveChange = (catalogId: string, event: React.ChangeEvent<HTMLInputElement>) => {
    if (!investigationId) return;
    const decision = event.target.value as "approved" | "rejected";
    setDecisions((prev) => ({ ...prev, [catalogId]: decision }));
    if (decision === "approved") {
      // approve: POST immediately with no note
      handleDecision(catalogId, decision, null);
    }
  };

  const onNoteChange = (catalogId: string, event: React.ChangeEvent<HTMLInputElement>) => {
    if (!investigationId) return;
    const value = event.target.value;
    setNotes((prev) => ({ ...prev, [catalogId]: value }));
    if (value.length < 10) {
      setNoteErrors((prev) => ({ ...prev, [catalogId]: "reason required" }));
    } else {
      setNoteErrors((prev) => {
        const copy = { ...prev };
        delete copy[catalogId];
        return copy;
      });
    }
  };

  const start = async () => {
    setRunning(true);
    try {
      const id = parseInt(investigationId!);
      // Session-only credentials stashed by the new-investigation form:
      // sent with the run start, then dropped from memory (#31).
      const run = await services.startRun(id, peekCredentials(id));
      clearCredentials(id);
      navigate(`/jobs/${run.job_id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "start error");
    } finally {
      setRunning(false);
    }
  };

  if (!investigation) return <p>Loading investigation…</p>;

  return (
    <main className="approval-screen">
      <h1>Approve Diagnostic Run</h1>
      {error && <p role="alert">{error}</p>}
      <section>{totalCount} query(ies) in this investigation</section>
      <section>
        {investigation.catalog.map((entry) => {
          const currentDecision = approvalResponse?.queries
            .find((q) => q.catalog_id === entry.id)
            ?.decision ?? "pending";
          const currentNote = approvalResponse?.queries
            .find((q) => q.catalog_id === entry.id)
            ?.note ?? "";
          const noteError = noteErrors[entry.id];
          // The DBA's in-progress choice wins over what the server has
          // recorded, so selecting Reject shows its reason box right away
          // (the rejection is POSTed once the reason qualifies, below).
          const effectiveDecision = decisions[entry.id] ?? currentDecision;
          return (
            <details key={entry.id} open>
              <summary>{entry.title || `Query ${entry.id}`}</summary>
              <QuerySql sql={entry.sql} />
              <p>
                <strong>Purpose:</strong> {entry.purpose}
              </p>
              <p>
                <strong>Expected Impact:</strong>{' '}
                {entry.expected_impact}
              </p>
              <div className="decision-controls">
                <label>
                  <input
                    type="radio"
                    name={`decision-${entry.id}`}
                    value="approved"
                    checked={effectiveDecision === "approved"}
                    disabled={readonly}
                    onChange={(e) => onApproveChange(entry.id, e)}
                  />{' '}
                  Approve
                </label>
                <label>
                  <input
                    type="radio"
                    name={`decision-${entry.id}`}
                    value="rejected"
                    checked={effectiveDecision === "rejected"}
                    disabled={readonly}
                    onChange={(e) => onApproveChange(entry.id, e)}
                  />{' '}
                  Reject
                </label>
                {effectiveDecision === "rejected" && (
                  <div>
                    <label>
                      Reason (min 10 chars):{' '}
                      <input
                        type="text"
                        value={notes[entry.id] ?? currentNote}
                        disabled={readonly}
                        onChange={(e) => onNoteChange(entry.id, e)}
                        aria-describedby={`reason-${entry.id}`}
                      />
                    </label>
                    {noteError && (
                      <p role="alert" id={`reason-${entry.id}`}>{noteError}</p>
                    )}
                  </div>
                )}
              </div>
            </details>
          );
        })}
      </section>
      <section>
        <p>
          {approvedCount} of {totalCount} approved
        </p>
        <button
          onClick={start}
          disabled={!(allApproved) || running || readonly}
        >
          {running ? "Starting…" : "Start run"}
        </button>
        {!allApproved && (
          <p>
            {rejectedQuery
              ? `rejected: ${rejectedQuery.note}`
              : "requires approval of all queries"}
          </p>
        )}
      </section>
    </main>
  );
};
