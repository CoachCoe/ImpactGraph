import { expect, test } from "@playwright/test";

test("donor can inspect the showcase claim and provenance", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /Good intentions deserve visible proof/i })).toBeVisible();
  await page.getByRole("link", { name: /Explore impact/i }).click();
  await expect(page.getByRole("heading", { name: /200 households gained access/i })).toBeVisible();
  await expect(page.getByRole("button", { name: /Inspect FUNDING/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Inspect FINANCIAL TRANSACTION/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: "INV-8291" })).toBeVisible();
  expect(await page.locator("body").evaluate((body) => body.scrollWidth <= window.innerWidth)).toBe(true);
});

test("operator evidence reaches confirmed state through the durable worker", async ({ page }) => {
  await page.goto("/operator");
  await page.getByRole("button", { name: "Load demo INV-8291" }).click();
  await page.getByRole("button", { name: "Upload & analyze" }).click();
  await expect(page.getByText("Invoice INV-8291")).toBeVisible();
  await expect(page.getByText("Vendor matches approved vendor")).toBeVisible();
  await page.getByRole("button", { name: "Accept & register evidence" }).click();
  await expect(page.getByText("✓ Evidence registration confirmed")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/^0x[0-9a-f]{64}$/)).toBeVisible();
  expect(await page.locator("body").evaluate((body) => body.scrollWidth <= window.innerWidth)).toBe(true);
});
