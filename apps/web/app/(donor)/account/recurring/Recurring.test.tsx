import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { formatDate, formatDateTime } from "@/lib/dates";
import RecurringPage from "./Recurring";

const subscription = {
  id: "owned-id",
  amount_minor: 3000,
  currency: "USD",
  status: "active",
  period_end: "2026-11-01T12:00:00Z",
  stops_at: null,
};
const response = (value: unknown) => ({ ok: true, json: async () => value });
afterEach(() => vi.unstubAllGlobals());
// Matches a paragraph by its whole text, which <time> elements split.
const paragraph = (text: string) => (_: string, node: Element | null) =>
  node?.tagName === "P" && node.textContent === text;

it("requires explicit confirmation and sends the displayed billing boundary", async () => {
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(response({ state: "ready", subscription }))
    .mockResolvedValueOnce(
      response({ state: "ready", subscription: { ...subscription, stops_at: subscription.period_end } }),
    );
  vi.stubGlobal("fetch", fetch);
  render(<RecurringPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Cancel monthly giving" }));
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(screen.getByText(/does not refund past gifts/)).toBeVisible();
  expect(screen.getByRole("heading", { name: "Confirm cancellation" })).toHaveFocus();
  fireEvent.click(screen.getByRole("button", { name: "Yes, stop renewals" }));
  await waitFor(() =>
    expect(screen.getByRole("status")).toHaveTextContent(
      `Renewals cancelled. Your last monthly gift period ends ${formatDate(subscription.period_end)}; no further gifts will be charged.`,
    ),
  );
  await waitFor(() => expect(screen.getByRole("heading", { level: 1, name: "My monthly giving" })).toHaveFocus());
  expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({
    subscription_id: "owned-id",
    period_end: subscription.period_end,
  });
  expect(fetch.mock.calls[1][1].headers["Idempotency-Key"]).toMatch(/^[a-f0-9-]{36}$/);
});

it("retries an uncertain cancellation with the same key and never claims success", async () => {
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(response({ state: "ready", subscription }))
    .mockRejectedValue(new Error("Connection lost"));
  vi.stubGlobal("fetch", fetch);
  render(<RecurringPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Cancel monthly giving" }));
  fireEvent.click(screen.getByRole("button", { name: "Yes, stop renewals" }));
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Cancellation is not confirmed"));
  expect(screen.queryByText(/Renewals cancelled/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Yes, stop renewals" }));
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3));
  expect(fetch.mock.calls[1][1].headers["Idempotency-Key"]).toBe(fetch.mock.calls[2][1].headers["Idempotency-Key"]);
});

it("offers a sign-in path when opened without a session", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({ ok: false, status: 401, json: async () => ({ detail: "Sign in required" }) }),
  );
  render(<RecurringPage />);
  expect(await screen.findByRole("link", { name: "Sign in to manage your giving" })).toHaveAttribute(
    "href",
    "/account/sign-in",
  );
  expect(screen.queryByRole("button", { name: "Cancel monthly giving" })).not.toBeInTheDocument();
});

it("distinguishes unconfirmed checkout from having no monthly gift", async () => {
  const fetch = vi.fn().mockResolvedValue(response({ state: "preparing", subscription: null }));
  vi.stubGlobal("fetch", fetch);
  render(<RecurringPage />);
  const copy = "We’re confirming your new monthly gift with Stripe. This usually takes a few seconds.";
  const callout = (await screen.findAllByText(copy)).find((node) => node.closest(".callout"));
  expect(callout).toBeVisible();
  expect(screen.getByRole("status")).toHaveTextContent(copy);
  expect(screen.queryByText("You have no monthly gift to manage.")).not.toBeInTheDocument();
  const sandbox = screen.getByText(/Sandbox only/);
  expect(callout?.compareDocumentPosition(sandbox) ?? 0).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  fireEvent.click(screen.getByRole("button", { name: "Refresh billing details" }));
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
});

it("treats a status the contract does not list as not active and offers no changes", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(response({ state: "ready", subscription: { ...subscription, status: "suspended" } })),
  );
  render(<RecurringPage />);
  expect(await screen.findByText("Your monthly gift is not active.")).toBeVisible();
  expect(screen.getByRole("link", { name: "Update payment method" })).toBeVisible();
  expect(screen.queryByRole("link", { name: "Change your daily gift" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Cancel monthly giving" })).not.toBeInTheDocument();
  expect(screen.queryByText(/being set up/)).not.toBeInTheDocument();
});

it("says a gift still being set up is being set up", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(response({ state: "ready", subscription: { ...subscription, status: "incomplete" } })),
  );
  render(<RecurringPage />);
  expect(await screen.findByText("Your monthly gift is still being set up. Refresh in a moment.")).toBeVisible();
  expect(screen.getByRole("button", { name: "Refresh billing details" })).toBeVisible();
  expect(screen.queryByRole("link", { name: "Change your daily gift" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Cancel monthly giving" })).not.toBeInTheDocument();
});

it("offers only a payment method update for an ending gift", async () => {
  const stops = subscription.period_end;
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(response({ state: "ready", subscription: { ...subscription, stops_at: stops } })),
  );
  render(<RecurringPage />);
  expect(
    await screen.findByText(
      paragraph(
        `Renewals cancelled. Your last monthly gift period ends ${formatDate(stops)}; no further gifts will be charged.`,
      ),
    ),
  ).toBeVisible();
  expect(screen.getByRole("link", { name: "Update payment method" })).toBeVisible();
  expect(screen.queryByRole("link", { name: "Change your daily gift" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Cancel monthly giving" })).not.toBeInTheDocument();
});

it("returns focus to the cancel control when backing out", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ state: "ready", subscription })));
  render(<RecurringPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Cancel monthly giving" }));
  fireEvent.click(screen.getByRole("button", { name: "Keep my monthly gift" }));
  expect(screen.getByRole("button", { name: "Cancel monthly giving" })).toHaveFocus();
});

it("dates billing details by their last Stripe sync and offers changes from them", async () => {
  const observed = "2026-10-30T12:00:00Z";
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ state: "ready", observed_at: observed, subscription })));
  render(<RecurringPage />);
  const synced = await screen.findByText(/last updated from Stripe/);
  expect(synced).toHaveTextContent(`Billing details last updated from Stripe ${formatDateTime(observed)}.`);
  expect(synced).toHaveClass("note");
  expect(screen.getByText(paragraph(`Next renewal: ${formatDate(subscription.period_end)}.`))).toBeVisible();
  expect(screen.getByRole("link", { name: "Change your daily gift" })).toBeVisible();
  expect(synced.querySelector("time")).toHaveAttribute("dateTime", observed);
  expect(screen.queryByRole("note")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Cancel monthly giving" })).toBeEnabled();
});

it("offers current billing details after Stripe refuses a cancellation shown from stored ones", async () => {
  const renewed = { ...subscription, period_end: "2026-12-01T12:00:00Z" };
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(response({ state: "ready", subscription }))
    .mockResolvedValueOnce({
      ok: false,
      status: 409,
      json: async () => ({ detail: "Billing details changed or require reconciliation. Refresh before confirming" }),
    })
    .mockResolvedValueOnce(response({ state: "ready", subscription: renewed }));
  vi.stubGlobal("fetch", fetch);
  render(<RecurringPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Cancel monthly giving" }));
  fireEvent.click(screen.getByRole("button", { name: "Yes, stop renewals" }));
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Billing details changed"));
  fireEvent.click(screen.getByRole("button", { name: "Refresh billing details" }));
  expect(await screen.findByText(paragraph(`Next renewal: ${formatDate(renewed.period_end)}.`))).toBeVisible();
});
