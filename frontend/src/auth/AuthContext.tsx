/**
 * Authentication state (#22).
 *
 * The provider resolves the caller by fetching the session indicator
 * (`GET /auth/session`) through the centralized services layer, which
 * attaches the bearer token stored by `login`. On mount, a stored token
 * is looked up; if it is absent or rejected (401), the app starts
 * unauthenticated and `/` redirects to the login screen.
 *
 * `signIn(username, password)` posts credentials to `POST /auth/login`,
 * stores the returned bearer token, and resolves the session. Sign-out
 * clears the token so the next reload starts fresh.
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
import { ApiError, services } from "../services";
import type { Session } from "../services/types";

export type AuthState =
  | { status: "loading" }
  | { status: "authenticated"; session: Session }
  | { status: "unauthenticated" };

/** The outcome of a sign-in attempt. */
export type SignInResult =
  | { ok: true }
  | { ok: false; message: string };

interface AuthContextValue {
  state: AuthState;
  signIn: (username: string, password: string) => Promise<SignInResult>;
  signOut: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: "loading" });

  // Resolve any stored session on mount.
  useEffect(() => {
    let cancelled = false;
    services
      .getSession()
      .then((session) => {
        if (!cancelled) setState({ status: "authenticated", session });
      })
      .catch(() => {
        if (!cancelled) setState({ status: "unauthenticated" });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const signIn = useCallback(
    async (username: string, password: string): Promise<SignInResult> => {
      try {
        const session = await services.login({ username, password });
        setState({ status: "authenticated", session });
        return { ok: true };
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) {
          return { ok: false, message: "Invalid username or password." };
        }
        if (error instanceof ApiError && error.status === 404) {
          return { ok: false, message: "Backend is unreachable." };
        }
        return { ok: false, message: "Could not sign in. Try again." };
      }
    },
    [],
  );

  const signOut = useCallback(() => {
    services.logout();
    setState({ status: "unauthenticated" });
  }, []);

  const value = useMemo(() => ({ state, signIn, signOut }), [state, signIn, signOut]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (context === null) {
    throw new Error("useAuth must be used inside <AuthProvider>");
  }
  return context;
}
