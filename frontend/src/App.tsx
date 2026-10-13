/**
 * Application shell.
 *
 * `/login` is public; every other route sits behind the session guard,
 * which redirects unauthenticated visitors to the login screen. The
 * route tree mirrors the investigation lifecycle:
 *
 *   /                            → hub (list + new)
 *   /investigations/new          → connection & scope form
 *   /investigations/:id/approve  → approval gate
 *   /jobs/:jobId                 → run status
 *   /investigations/:id/report   → report dashboard
 */

import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { RequireAuth } from "./auth/RequireAuth";
import { ApprovalScreen } from "./pages/ApprovalScreen";
import { ConnectionScopeForm } from "./pages/ConnectionScopeForm";
import { JobStatusPage } from "./pages/JobStatusPage";
import { LandingPage } from "./pages/LandingPage";
import { LoginPage } from "./pages/LoginPage";
import { ReportPage } from "./pages/ReportPage";

export function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <header className="app-header">
          <span className="brand">SDIP</span>
          <span className="brand-sub">SQL Server Diagnostics</span>
        </header>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route element={<RequireAuth />}>
            <Route path="/" element={<LandingPage />} />
            <Route
              path="/investigations"
              element={<Navigate to="/" replace />}
            />
            <Route
              path="/investigations/new"
              element={<ConnectionScopeForm />}
            />
            <Route
              path="/investigations/:id/approve"
              element={<ApprovalScreen />}
            />
            <Route path="/jobs/:jobId" element={<JobStatusPage />} />
            <Route
              path="/investigations/:id/report"
              element={<ReportPage />}
            />
          </Route>
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
