/**
 * Layout guard for authenticated routes (#22/#23).
 *
 * Unauthenticated access to any protected route redirects
 * to the login screen; a resolved session renders the
 * child routes via the outlet.
 */

import { Navigate, Outlet } from "react-router-dom";
import { useAuth } from "./AuthContext";

export function RequireAuth() {
  const { state } = useAuth();

  if (state.status === "loading") {
    return <p>Loading…</p>;
  }
  if (state.status === "unauthenticated") {
    return <Navigate to="/login" replace />;
  }
  return <Outlet />;
}
