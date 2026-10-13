/**
 * The login screen (#22).
 *
 * Posts username/password to `POST /auth/login` via the services layer,
 * which stores the returned bearer token. Demo credentials are shown
 * because this is a seeded, read-only diagnostic tool.
 */

import { useState } from "react";
import type { FormEvent } from "react";
import { useAuth } from "../auth/AuthContext";

export function LoginPage() {
  const { signIn } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setPending(true);
    setMessage(null);
    const result = await signIn(username, password);
    setPending(false);
    if (result.ok) return;
    setMessage(result.message);
  };

  return (
    <main className="login">
      <h1>SQL Server Diagnostic Assistant</h1>
      <p>Sign in to start a read-only investigation.</p>
      <form onSubmit={onSubmit}>
        <label htmlFor="username">Username</label>
        <input
          id="username"
          name="username"
          autoComplete="username"
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          required
        />
        <label htmlFor="password">Password</label>
        <input
          id="password"
          name="password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          required
        />
        <button type="submit" disabled={pending || !username || !password}>
          {pending ? "Signing in…" : "Sign in"}
        </button>
      </form>
      <p className="hint">
        Demo: <code>dba / dba-password</code> or <code>admin / admin-password</code>
      </p>
      {message ? <p role="alert">{message}</p> : null}
    </main>
  );
}
