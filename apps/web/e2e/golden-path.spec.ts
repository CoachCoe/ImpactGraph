import { expect, test, type Page } from "@playwright/test";

/**
 * The two journeys the product is demonstrated on.
 *
 * CI does not run these, which is how they were able to rot: the operator journey
 * navigated straight to a page that has required a session ever since authentication
 * replaced the role header, and both asserted copy that had since changed. A suite the
 * README tells people to run before touching the chain path has to actually pass.
 */

const OPERATOR = "operator@globalwater.example";
const PASSWORD = "impactgraph-demo";

async function signIn(page: Page, email: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

async function expectNoHorizontalScroll(page: Page): Promise<void> {
  const fits = await page
    .locator("body")
    .evaluate((body) => body.scrollWidth <= window.innerWidth + 1);
  expect(fits, "the page scrolls sideways at this viewport").toBe(true);
}

test("a donor can follow the money to what it reached, without an account", async ({
  page,
}) => {
  await page.goto("/");
  // Not the header navigation: it is hidden below 800px with nothing in its place, so a
  // donor on a phone reaches the money trail through the page itself. See issue #22.
  await expect(page.getByText(/Recorded funding/i).first()).toBeVisible();
  await expectNoHorizontalScroll(page);

  await page
    .getByRole("link", { name: /See where the money went/i })
    .first()
    .click();
  await expect(page).toHaveURL(/\/financial/);

  await page.getByRole("link", { name: /Jane Smith/ }).first().click();
  await expect(page).toHaveURL(/\/funding\//);
  await expect(page.getByRole("heading", { name: /Jane Smith gave/i })).toBeVisible();
  // Money that never moved is part of the answer, so it has to be on the page.
  await expect(
    page.getByText(/Not yet committed|Committed beyond funding/).first(),
  ).toBeVisible();
  await expectNoHorizontalScroll(page);
});

test("a donor can read the claim and check its evidence, without an account", async ({
  page,
}) => {
  await page.goto("/claims/claim-water-12-200");
  await expect(
    page.getByRole("heading", { name: /200 households gained access/i }),
  ).toBeVisible();
  await expectNoHorizontalScroll(page);

  await page.goto("/evidence/ev-inv-8291");
  await page.getByRole("button", { name: /Check this yourself/i }).click();
  // The verdict reaches assistive technology through the seal's accessible name rather
  // than as text, so asserting the role checks both that it matched and that it is
  // announced.
  await expect(
    page.getByRole("img", { name: /Byte-for-byte match/i }),
  ).toBeVisible({ timeout: 20_000 });
  await expectNoHorizontalScroll(page);
});

test("operator evidence reaches confirmed state through the durable worker", async ({
  page,
}) => {
  await signIn(page, OPERATOR);

  await page.goto("/operator");
  await page.getByRole("button", { name: "Load demo INV-8291" }).click();
  await page.getByRole("button", { name: "Upload & analyze" }).click();

  await expect(page.getByText("Invoice INV-8291")).toBeVisible({ timeout: 20_000 });
  // Reconciliation names the payment it resolved from the ledger. It does not assert that
  // a vendor is approved, which is a judgement this system never makes.
  await expect(page.getByText(/Vendor matches the payee of/)).toBeVisible();

  await page.getByRole("button", { name: "Accept & register evidence" }).click();
  // Not complete until the backend has independently observed the expected registry event.
  await expect(page.getByText("Evidence registration confirmed")).toBeVisible({
    timeout: 60_000,
  });
  await expectNoHorizontalScroll(page);
});
