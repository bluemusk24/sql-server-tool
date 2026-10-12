/**
 * The login screen (#22).
 *
 * The "Sign in with SSO" button starts the organization's OIDC
 * flow by hitting `GET /auth/authorize`. Until #30 deploys that
 * endpoint, the button surfaces a clear "not deployed" notice
 * instead of crashing.
 */

import { useState } from "react";
import type { FormEvent } from "react";
import { useAuth } from "../auth/AuthContext";

export function LoginPage() {
  const { signIn } = useAuth();
  const [message, setMessage] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const onSignIn = async (event: FormEvent) => {
    event.preventDefault();
    setPending(true);
    setMessage(null);
    const result = await signIn();
    setPending(false);
    if (result.ok) {
      // #30 returns the authorize URL; the browser completes the flow.
      window.location.href = result.authorizeUrl;
      return;
    }
    setMessage(result.message);
  };

  return (
    <main className="login">
      <h1>SQL Server Diagnostic Assistant</h1>
      <p>
        Sign in with your organization&rsquo;s single sign-on to start an
        investigation.
      </p>
      <form onSubmit={onSignIn}>
        <button type="submit" disabled={pending}>
          {pending ? "Signing in…" : "Sign in with SSO"}
        </button>
      </form>
      {message ? <p role="alert">{message}</p> : null}
    </main>
  );
}
