/**
 * In-memory, session-only stash for per-investigation SQL credentials (#31).
 *
 * docs/spec.md §6: credentials must never be stored in history, reports,
 * or logs — and never in this app's persisted storage either. This map
 * lives in the tab's JS heap only: nothing serialises it to disk, and the
 * entry is deleted once the run has started (or the screen goes away).
 */

import type { Credentials } from "./types";

const stash = new Map<number, Credentials>();

/** Remember credentials between the form and the approval screen. */
export function stashCredentials(
  investigationId: number,
  credentials: Credentials,
): void {
  stash.set(investigationId, credentials);
}

/** The stashed credentials for an investigation, if any. */
export function peekCredentials(
  investigationId: number,
): Credentials | null {
  return stash.get(investigationId) ?? null;
}

/** Drop them (after a successful run start, or to clean up). */
export function clearCredentials(investigationId: number): void {
  stash.delete(investigationId);
}
