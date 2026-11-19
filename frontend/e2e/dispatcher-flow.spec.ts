import { test, expect, type Page } from "@playwright/test";

const TEST_EMAIL = process.env.E2E_ADMIN_EMAIL ?? "admin@delivery.com";
const TEST_PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? "admin123";
const TENANT_SLUG = process.env.E2E_TENANT_SLUG ?? "default";

async function login(page: Page) {
  await page.goto("/login");
  await page.fill("#signin-email", TEST_EMAIL);
  await page.fill("#signin-password", TEST_PASSWORD);
  await page.fill("#signin-workspace", TENANT_SLUG);
  await page.click('button[type="submit"]');
  await page.waitForURL("/", { timeout: 10_000 });
}

test.describe("Dispatcher login flow", () => {
  test("shows error message on wrong password", async ({ page }) => {
    await page.goto("/login");
    await page.fill("#signin-email", TEST_EMAIL);
    await page.fill("#signin-password", "definitely-wrong-password");
    await page.fill("#signin-workspace", TENANT_SLUG);
    await page.click('button[type="submit"]');

    await expect(page.locator('[data-testid="auth-error"]')).toBeVisible({ timeout: 10_000 });
    // URL must remain on login — no navigation happened
    await expect(page).toHaveURL(/\/login/);
  });

  test("login and reach LiveOps dashboard", async ({ page }) => {
    await login(page);
    await expect(
      page.locator('[data-testid="stats-grid"]').or(page.locator("text=Live Ops")),
    ).toBeVisible({ timeout: 15_000 });
  });

  test("navigate to orders page and verify table and header", async ({ page }) => {
    await login(page);
    await page.goto("/orders");
    await expect(page.locator("h1")).toContainText("Orders");
    // The New Order button must be present
    await expect(page.locator('[data-testid="create-order-btn"]')).toBeVisible({ timeout: 10_000 });
  });

  test("navigate to SLA alerts page", async ({ page }) => {
    await login(page);
    await page.goto("/sla-alerts");
    await expect(page.locator("h1")).toContainText("SLA Alerts");
  });

  test("navigate to couriers page and see Add Courier button", async ({ page }) => {
    await login(page);
    await page.goto("/couriers");
    await expect(page.locator("h1")).toContainText("Couriers");
    await expect(page.locator("button", { hasText: "Add Courier" })).toBeVisible();
  });

  test("analytics page shows time range tabs", async ({ page }) => {
    await login(page);
    await page.goto("/analytics");
    await expect(page.locator("h1")).toContainText("Analytics");
    await expect(page.locator("button", { hasText: "Today" })).toBeVisible();
    await expect(page.locator("button", { hasText: "This Week" })).toBeVisible();
  });
});
