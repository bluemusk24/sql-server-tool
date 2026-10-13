/**
 * Session-only credentials stash tests (#31).
 *
 * The stash is the only place a SQL password lives between the form and
 * the run start. It must round-trip values, drop them on clear, and be
 * empty by default — and it must never be anything but heap memory.
 */

import { afterEach, describe, expect, it } from "vitest";
import {
  clearCredentials,
  peekCredentials,
  stashCredentials,
} from "../credentials";

afterEach(() => {
  clearCredentials(1);
  clearCredentials(2);
});

describe("credentials stash", () => {
  it("round-trips credentials for an investigation", () => {
    stashCredentials(1, { username: "svc_diag", password: "s3cret" });
    expect(peekCredentials(1)).toEqual({
      username: "svc_diag",
      password: "s3cret",
    });
  });

  it("returns null when nothing was stashed", () => {
    expect(peekCredentials(2)).toBeNull();
  });

  it("forgets credentials once cleared", () => {
    stashCredentials(1, { username: null, password: "s3cret" });
    clearCredentials(1);
    expect(peekCredentials(1)).toBeNull();
  });
});
