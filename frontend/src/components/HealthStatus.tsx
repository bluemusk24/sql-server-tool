/**
 * API health indicator (#22).
 *
 * Calls the real `GET /health` endpoint (#2) and shows its
 * status, so the shell proves the API base URL is reachable.
 */

import { useEffect, useState } from "react";
import { getHealth } from "../api/client";
import type { HealthStatus } from "../api/types";

type State =
  | { status: "loading" }
  | { status: "ok"; health: HealthStatus }
  | { status: "error"; message: string };

export function HealthStatus() {
  const [state, setState] = useState<State>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    getHealth()
      .then((health) => {
        if (!cancelled) {
          setState({ status: "ok", health });
        }
      })
      .catch((error: unknown) => {
        if (cancelled) {
          return;
        }
        const message =
          error instanceof Error ? error.message : "health check failed";
        setState({ status: "error", message });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (state.status === "loading") {
    return <p>API status: checking…</p>;
  }
  if (state.status === "error") {
    return <p>API status: unavailable ({state.message})</p>;
  }
  return <p>API status: {state.health.status} (v{state.health.version})</p>;
}
