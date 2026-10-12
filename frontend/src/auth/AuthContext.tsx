/**
 * Authentication state (#22).
 *
 * The provider resolves the caller by fetching the session
 * indicator (`GET /auth/session`). Until #30 deploys the
 * backend flow that endpoint returns 404, so the app starts
 * unauthenticated and `/` redirects to the login screen.
 *
 * `signIn` starts the OIDC flow by hitting `GET /auth/authorize`.
 * That endpoint is also #30's, so a 404 here is expected and is
 * surfaced as a clear notice rather than crashing the app.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import type { ReactNode } from "react";
import { ApiError, getSession, startAuthorization } from "../api/client";
import type { Session } from "../api/types";

export type AuthState =
  | { status: "loading" }
  | { status: "authenticated"; session: Session }
  | { status: "unauthenticated" };

/** The outcome of a sign-in attempt. */
export type SignInResult =
  | { ok: true; authorizeUrl: string }
  | { ok: false; message: string };

interface AuthContextValue {
  state: AuthState;
  signIn: () => Promise<SignInResult>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    getSession()
      .then((session) => {
        if (!cancelled) {
          setState({ status: "authenticated", session });
        }
      })
      .catch(() => {
        if (!cancelled) {
          setState({ status: "unauthenticated" });
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const signIn = useCallback(async (): Promise<SignInResult> => {
    try {
      const start = await startAuthorization();
      return { ok: true, authorizeUrl: start.authorize_url };
    } catch (error) {
      // Pre-#30 the endpoint is not deployed (404): show a clear,
      // non-crashing notice. Anything else is a generic retry hint.
      if (error instanceof ApiError && error.status === 404) {
        return { ok: false, message: "Backend sign-in is not deployed yet." };
      }
      return { ok: false, message: "Could not start sign-in. Try again." };
    }
  }, []);

  const value = useMemo(() => ({ state, signIn }), [state, signIn]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (context === null) {
    throw new Error("useAuth must be used inside <AuthProvider>");
  }
  return context;
}
