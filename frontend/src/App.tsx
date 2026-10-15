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

import {
  BrowserRouter,
  NavLink,
  Navigate,
  Route,
  Routes,
  useLocation,
} from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { RequireAuth } from "./auth/RequireAuth";
import { ApprovalScreen } from "./pages/ApprovalScreen";
import { ConnectionScopeForm } from "./pages/ConnectionScopeForm";
import { JobStatusPage } from "./pages/JobStatusPage";
import { LandingPage } from "./pages/LandingPage";
import { LoginPage } from "./pages/LoginPage";
import { ReportPage } from "./pages/ReportPage";

/**
 * The header carries the product mark plus the two primary destinations.
 * The nav is hidden on `/login`, where there is nothing yet to navigate
 * to (the session guard would only bounce back).
 */
function AppHeader() {
  const { pathname } = useLocation();
  const showNav = pathname !== "/login";

  return (
    <header className="app-header">
      <NavLink className="brand" to="/">
        SDIP
      </NavLink>
      <span className="brand-sub">SQL Server Diagnostics</span>
      {showNav && (
        <nav className="app-nav" aria-label="Primary">
          <NavLink to="/" end>
            Queue
          </NavLink>
          <NavLink to="/investigations/new">New</NavLink>
        </nav>
      )}
    </header>
  );
}

export function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <AppHeader />
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
