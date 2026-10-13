/**
 * Bearer-token storage for the real backend.
 *
 * `POST /auth/login` returns an opaque token; the frontend must send it
 * on every protected request as `Authorization: Bearer <token>`. The
 * token is kept in `localStorage` so a page reload keeps the session.
 *
 * This module is only used by the real HTTP transport — the mock keeps
 * its session in memory and never touches storage.
 */

const TOKEN_KEY = "sda.auth_token";

export function getToken(): string | null {
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    // Storage can be unavailable (private mode, blocked cookies).
    return null;
  }
}

export function setToken(token: string): void {
  try {
    window.localStorage.setItem(TOKEN_KEY, token);
  } catch {
    // Ignore: the app still works for this tab, just not across reloads.
  }
}

export function clearToken(): void {
  try {
    window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    // Ignore.
  }
}
