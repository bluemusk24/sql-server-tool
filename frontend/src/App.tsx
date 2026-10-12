/**
 * Application shell (#22).
 *
 * `/login` is public; `/` is protected by the session guard and
 * redirects unauthenticated visitors to the login screen.
 */

import { BrowserRouter, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { ProtectedRoute } from "./auth/ProtectedRoute";
import { LoginPage } from "./pages/LoginPage";

export function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={<ProtectedRoute />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
