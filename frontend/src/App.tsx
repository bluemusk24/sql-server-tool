/**
 * Application shell (#22/#23).
 *
 * `/login` is public; every other route sits behind the
 * session guard, which redirects unauthenticated visitors
 * to the login screen.
 */

import { BrowserRouter, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { RequireAuth } from "./auth/RequireAuth";
import { ConnectionScopeForm } from "./pages/ConnectionScopeForm";
import { LandingPage } from "./pages/LandingPage";
import { LoginPage } from "./pages/LoginPage";

export function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route element={<RequireAuth />}>
            <Route path="/" element={<LandingPage />} />
            <Route
              path="/investigations/new"
              element={<ConnectionScopeForm />}
            />
          </Route>
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
