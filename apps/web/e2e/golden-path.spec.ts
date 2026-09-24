import { expect, test, type Page } from "@playwright/test";

/**
 * The two journeys the product is demonstrated on.
 *
 * These rotted once because nothing ran them: the operator journey navigated straight to
 * a page that has required a session ever since authentication replaced the role header,
 * and both asserted copy that had since changed. CI now runs them against the demo stack
 * on every push, so the next time they drift it is a red build rather than a discovery.
 */

const OPERATOR = "operator@globalwater.example";
const CLAIM = "claim-water-12-200";

/** What the demo invoice says, for any field the model did not read off it. */
const DOCUMENT: Record<string, string> = {
  "Document type": "invoice",
  "Invoice number": "INV-8291",
  Vendor: "Aqua Systems Ltd.",
  "Amount (minor units)": "420000",
  Currency: "USD",
  Date: "2026-08-17",
  Equipment: "AquaPure X200",
  Quantity: "2",
  Project: "Water Project #12",
};
const PASSWORD = "impactgraph-demo";

async function signIn(page: Page, email: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

/**
 * Reach a header destination the way a reader at this viewport has to.
 *
 * Above 800px the bar is on the page; below it the same links sit behind a button.
 * One helper covers both so the journeys read the same in either project, and so the
 * mobile path is exercised rather than stepped around.
 */
async function navigateByHeader(page: Page, name: RegExp): Promise<void> {
  const menu = page.getByRole("button", { name: "Menu" });
  if (await menu.isVisible()) {
    await menu.click();
    await expect(menu).toHaveAttribute("aria-expanded", "true");
  }
  await page
    .getByRole("navigation", { name: "Primary navigation" })
    .getByRole("link", { name })
    .click();
}

/**
 * Open a program from the landing page.
 *
 * A deployment with one program shows its record directly; one with several asks which,
 * because a donor arriving at the front door of a multi-tenant system has to say whose
 * money they are following. Both are real deployments and the journey must work on either.
 */
async function openProgram(page: Page): Promise<void> {
  await page.goto("/");
  const chooser = page.getByRole("heading", { name: "Choose a program" });
  if (await chooser.isVisible().catch(() => false)) {
    await page.locator(".programRow").first().click();
  }
  await expect(page.getByText(/Recorded funding/i).first()).toBeVisible();
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
  await openProgram(page);
  await expectNoHorizontalScroll(page);

  // The contribution is the front door: one click opens its own allocation trail rather
  // than making a donor find it in a program-wide ledger first.
  await expect(
    page.getByRole("heading", { name: /received.*committed.*households/i }),
  ).toBeVisible();
  await expect(page.getByText("Jane Smith")).toHaveCount(0);
  await page.getByRole("link", { name: /Inspect every record/i }).click();
  await expect(page).toHaveURL(/\/funding\//);
  await expect(
    page.getByRole("heading", { name: /An individual donor gave/i }),
  ).toBeVisible();
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

  // Not asserted against the model's output. A model reads the document afresh each time
  // and has been observed returning seven of the nine fields for these same bytes, so a
  // test that pinned the invoice number would be testing the model rather than the
  // product. What must hold is the product's guarantee: whatever was read, a person
  // confirms every flagged field, filling in anything the model could not read, and only
  // then can it be registered.
  const register = page.getByRole("button", {
    name: /Accept & register evidence|Confirm \d+ field/,
  });
  await expect(register).toBeVisible({ timeout: 30_000 });
  await expect(
    register,
    "registration is open before anyone confirmed anything",
  ).toBeDisabled();

  for (const [label, value] of Object.entries(DOCUMENT)) {
    const confirm = page.getByRole("checkbox", { name: `Confirm ${label}` });
    if ((await confirm.count()) === 0) continue;
    const field = page.getByLabel(label, { exact: true });
    if ((await field.inputValue()) === "") await field.fill(value);
    await confirm.check();
  }

  await expect(register).toBeEnabled();
  await register.click();
  // Not complete until the backend has independently observed the expected registry event.
  await expect(page.getByText("Evidence registration confirmed")).toBeVisible({
    timeout: 60_000,
  });
  await expectNoHorizontalScroll(page);
});

test("every header destination is reachable at this viewport", async ({
  page,
}) => {
  // The regression this guards: below 800px the navigation was hidden and nothing
  // replaced it, so /about -- the page explaining how any of this can be checked --
  // could not be reached on a phone at all. Issue #22.
  await page.goto("/");

  await navigateByHeader(page, /^How it works$/);
  await expect(page).toHaveURL(/\/about/);
  await expect(page.getByRole("heading", { name: /Prove it/i })).toBeVisible();
  await expectNoHorizontalScroll(page);

  await navigateByHeader(page, /^Money trail$/);
  await expect(page).toHaveURL(/\/financial/);

  await navigateByHeader(page, /^Donor$/);
  await expect(page).toHaveURL(/\/$|\/\?/);
});

test("the menu button announces its state, and Escape closes it", async ({
  page,
}) => {
  await page.goto("/");
  const menu = page.getByRole("button", { name: "Menu" });
  test.skip(
    !(await menu.isVisible()),
    "the bar is on the page at this viewport",
  );

  await expect(menu).toHaveAttribute("aria-expanded", "false");
  const links = page.getByRole("navigation", { name: "Primary navigation" });
  await expect(links).toBeHidden();

  await menu.click();
  await expect(menu).toHaveAttribute("aria-expanded", "true");
  await expect(links).toBeVisible();
  await expect(
    links.getByRole("link", { name: /^How it works$/ }),
  ).toBeVisible();
  await expectNoHorizontalScroll(page);

  await page.keyboard.press("Escape");
  await expect(menu).toHaveAttribute("aria-expanded", "false");
  await expect(links).toBeHidden();
  // Escape that leaves focus nowhere strands a keyboard reader mid-page.
  await expect(menu).toBeFocused();
});

test("an operator can move between their screens on a phone", async ({
  page,
}) => {
  // The journey most likely to happen on a phone, and the one the hidden navigation
  // broke hardest: an operator in the field had no way off whichever page they landed on.
  await signIn(page, OPERATOR);
  await page.goto("/");

  await navigateByHeader(page, /^Operator$/);
  await expect(page).toHaveURL(/\/operator/);

  await navigateByHeader(page, /^Money trail$/);
  await expect(page).toHaveURL(/\/financial/);
  await expectNoHorizontalScroll(page);
});

test("a published claim can be landed on cold, and says what it does not prove", async ({
  page,
  request,
}) => {
  // Published by its operator first: a claim nobody published has no proof page, which is
  // what makes publication a decision rather than a default.
  await request.post("/api/auth/login", {
    data: { email: OPERATOR, password: PASSWORD },
  });
  await request.post(`/api/claims/${CLAIM}/publish`);

  // No session, no referrer, no context -- the way a funder arrives from a grant
  // application or a link in an email.
  await page.goto(`/proof/${CLAIM}`);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  // Twice on the page, as the byline and as the attestor; both are legitimate.
  await expect(page.getByText("Global Water Initiative").first()).toBeVisible();

  // A page listing only what passed would be an advertisement.
  await expect(
    page.getByRole("heading", { name: "What it does not" }),
  ).toBeVisible();
  await expect(page.getByText(/actually helped anyone/)).toBeVisible();

  // And the badge an organisation would embed is reachable and really is an image.
  const badge = await page.request.get(`/api/claims/${CLAIM}/badge.svg`);
  expect(badge.status()).toBe(200);
  expect(badge.headers()["content-type"]).toContain("image/svg+xml");
  expect(badge.headers()["cache-control"]).toContain("must-revalidate");

  await expectNoHorizontalScroll(page);
});

test("the proof page does not name the individual who funded the work", async ({
  page,
  request,
}) => {
  await request.post("/api/auth/login", {
    data: { email: OPERATOR, password: PASSWORD },
  });
  await request.post(`/api/claims/${CLAIM}/publish`);

  await page.goto(`/proof/${CLAIM}`);
  await expect(page.getByText("Jane Smith")).toHaveCount(0);
});
