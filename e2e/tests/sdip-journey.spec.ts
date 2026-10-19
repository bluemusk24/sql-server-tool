/**
 * Two-session hand-off journey — the product's core workflow.
 *
 * SDIP's version of the "two clients" pattern:
 *
 *   Session 1 (DBA)          Session 2 (approver)
 *   ─────────────────        ────────────────────
 *   log in as dba
 *   create investigation  ──▶ shared approval link
 *                             opens the link (separate
 *                             browser context = separate
 *                             session), logs in as admin,
 *                             reviews the generated SQL and
 *   sees status flip to        approves every query, then
 *   completed, report   ◀──   starts the run
 *   renders
 *
 * Session 2 deliberately holds no connection credentials (they live
 * only in session 1's memory), so the run takes the demo path — the
 * same honest behavior a fresh browser gets in real use.
 */

import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const DBA = { username: "dba", password: "dba-password" };
const ADMIN = { username: "admin", password: "admin-password" };
// Unique per run: repeated runs against a persistent database must not
// collide on the list-page row filter (strict-mode needs ONE match).
const TARGET_HOST = `sql-e2e-${Date.now()}.example`;

async function login(
  page: Page,
  user: { username: string; password: string },
): Promise<void> {
  await page.goto("/login");
  await page.getByLabel(/username/i).fill(user.username);
  await page.getByLabel(/password/i).fill(user.password);
  await page.getByRole("button", { name: /sign in/i }).click();
  // Signed in lands on the hub.
  await expect(page).toHaveURL(/\/$/);
}

test("anonymous visitors are sent to the login screen", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login/);
  await expect(page.getByRole("button", { name: /sign in/i })).toBeVisible();
});

test("DBA creates an investigation, approver reviews and runs it, DBA sees the report", async ({
  browser,
}) => {
  // ── Session 1: the DBA ───────────────────────────────────────────
  const session1 = await browser.newContext();
  const dba = await session1.newPage();
  await login(dba, DBA);

  await dba.goto("/investigations/new");
  await dba.getByLabel(/server\/database host/i).fill(TARGET_HOST);
  await dba.getByLabel(/^port/i).fill("1433");
  await dba.getByLabel(/username/i).fill("svc-e2e");
  // Memory-only per #23/#31: never part of the payload, and this
  // session never starts the run, so it is never used.
  await dba.getByLabel(/password/i).fill("not-a-real-password");
  await dba.getByRole("button", { name: /create investigation/i }).click();

  // The form hands off to the approval gate — that URL is the link
  // the DBA would share with the approver.
  await dba.waitForURL(/\/investigations\/\d+\/approve$/);
  const approvalUrl = dba.url();
  await expect(
    dba.getByRole("heading", { name: /approve diagnostic run/i }),
  ).toBeVisible();
  await expect(dba.getByText(`${TARGET_HOST}:1433`)).toBeVisible();

  // ── Session 2: a separate client opens the shared link ───────────
  const session2 = await browser.newContext();
  const approver = await session2.newPage();
  // A fresh context has no token: sign in first, then open the link.
  await login(approver, ADMIN);
  await approver.goto(approvalUrl);

  await expect(
    approver.getByRole("heading", { name: /approve diagnostic run/i }),
  ).toBeVisible();
  await expect(approver.getByText(`${TARGET_HOST}:1433`)).toBeVisible();

  // Review every generated query card and approve it; each choice is
  // POSTed immediately.
  const approveRadios = approver.locator(
    'input[type="radio"][value="approved"]',
  );
  const total = await approveRadios.count();
  expect(total).toBeGreaterThan(0);
  for (let i = 0; i < total; i += 1) {
    await approveRadios.nth(i).click();
  }
  await expect(
    approver.getByText(`${total} of ${total} approved`),
  ).toBeVisible();

  // Start the run and watch it reach a terminal state. Session 2 has
  // no stashed credentials, so the backend takes the demo path and
  // the job succeeds.
  await approver.getByRole("button", { name: /start run/i }).click();
  await approver.waitForURL(/\/jobs\/[^/]+$/);
  await expect(approver.getByTestId("job-state")).toHaveText("succeeded");
  await approver.getByRole("link", { name: /view report/i }).click();
  await expect(
    approver.getByRole("heading", { name: /diagnostic report/i }),
  ).toBeVisible();

  // ── Back to session 1: the cross-session result is visible ───────
  await dba.goto("/");
  const rows = dba.locator(".investigation-list li", {
    hasText: TARGET_HOST,
  });
  // Exactly one row — this run's investigation, no look-alikes.
  await expect(rows).toHaveCount(1);
  // The lifecycle pill flipped to completed for the DBA too.
  await expect(rows.locator('strong[data-state="completed"]')).toBeVisible();
  await rows.getByRole("link").click();
  await expect(
    dba.getByRole("heading", { name: /diagnostic report/i }),
  ).toBeVisible();
  await expect(dba.getByText(`${TARGET_HOST}:1433`)).toBeVisible();

  await session1.close();
  await session2.close();
});
