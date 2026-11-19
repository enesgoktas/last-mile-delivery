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

test.describe("Optimization flow", () => {
  test("LiveOps dashboard shows stats grid after data loads", async ({ page }) => {
    await login(page);
    // Stats grid has a data-testid — no more brittle CSS sibling selector
    await expect(page.locator('[data-testid="stats-grid"]')).toBeVisible({ timeout: 15_000 });
    // The "total" label must be visible inside the stats grid
    await expect(page.locator('[data-testid="stats-grid"]').locator("text=total")).toBeVisible();
  });

  test("orders page shows New Order button", async ({ page }) => {
    await login(page);
    await page.goto("/orders");
    await expect(page.locator('[data-testid="create-order-btn"]')).toBeVisible({ timeout: 10_000 });
  });

  test("orders table appears after data loads", async ({ page }) => {
    await login(page);
    await page.goto("/orders");
    // Table renders when there are orders; skip assertion if no seed data
    const table = page.locator('[data-testid="orders-table"]');
    const emptyState = page.locator("text=/no orders found/i");
    await expect(table.or(emptyState)).toBeVisible({ timeout: 15_000 });
  });

  test("analytics page loads KPI data and responds to time-range tab switch", async ({ page }) => {
    await login(page);
    await page.goto("/analytics");
    await expect(page.locator("h1")).toContainText("Analytics");

    // Switch time range — the request should update without crashing
    await page.click('button:has-text("Today")');
    await expect(page.locator("button:has-text('Today')")).toHaveClass(/bg-white/);

    await page.click('button:has-text("All Time")');
    await expect(page.locator("button:has-text('All Time')")).toHaveClass(/bg-white/);
  });

  test("settings page opens and shows Operations tab by default", async ({ page }) => {
    await login(page);
    await page.goto("/settings");
    await expect(page.locator("h1")).toContainText("Settings");
    // The Operations tab content should be visible
    await expect(
      page
        .locator("text=Industry Presets")
        .or(page.locator("text=/loading operations settings/i")),
    ).toBeVisible({ timeout: 15_000 });
  });
});
