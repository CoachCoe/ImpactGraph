import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import AppReturnPage from "./page";

async function renderWith(params: Record<string, string | string[] | undefined>) {
  render(await AppReturnPage({ params: Promise.resolve({}), searchParams: Promise.resolve(params) }));
}

it("tells a donor who paid to return to the app and promises nothing the webhook has not settled", async () => {
  await renderWith({ result: "success", session_id: "cs_test_123" });
  expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("You can return to the app");
  expect(screen.getByText(/once Stripe confirms the payment/)).toBeVisible();
  expect(screen.queryByText(/cs_test_123/)).toBeNull();
});

it("says nothing was charged when the donor backed out", async () => {
  await renderWith({ result: "cancel" });
  expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Checkout cancelled");
  expect(screen.getByText(/nothing was charged/)).toBeVisible();
});

it.each([{}, { result: "unexpected" }, { result: ["cancel", "success"] }])(
  "falls back to the return notice for %j",
  async (params) => {
    await renderWith(params);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("You can return to the app");
  },
);
