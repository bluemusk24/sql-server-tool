/**
 * API health indicator (#22).
 *
 * Calls the real `GET /health` endpoint (#2) and shows its
 * status, so the shell proves the API base URL is reachable.
 */

import { useEffect, useState } from "react";
import { services } from "../services";
import type { HealthStatus } from "../services/types";

type State =
  | { status: "loading" }
  | { status: "ok"; health: HealthStatus }
  | { status: "error"; message: string };

export function HealthStatus() {
  const [state, setState] = useState<State>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    services
      .getHealth()
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
    return (
      <p className="health" data-state="checking">
        API status: checking…
      </p>
    );
  }
  if (state.status === "error") {
    return (
      <p className="health" data-state="error">
        API status: unavailable ({state.message})
      </p>
    );
  }
  return (
    <p className="health" data-state={state.health.status}>
      API status: {state.health.status} (v{state.health.version})
    </p>
  );
}
