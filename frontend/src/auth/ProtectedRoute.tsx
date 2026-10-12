/**
 * Route guard for authenticated pages (#22).
 *
 * Unauthenticated access to `/` redirects to the login screen.
 * The landing page is only reachable with a resolved session.
 */

import { Navigate } from "react-router-dom";
import { useAuth } from "./AuthContext";
import { LandingPage } from "../pages/LandingPage";

export function ProtectedRoute() {
  const { state } = useAuth();

  if (state.status === "loading") {
    return <p>Loading…</p>;
  }
  if (state.status === "unauthenticated") {
    return <Navigate to="/login" replace />;
  }
  return <LandingPage session={state.session} />;
}
