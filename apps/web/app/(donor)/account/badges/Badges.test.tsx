import { render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import Badges from "./Badges";

afterEach(() => vi.unstubAllGlobals());

it("separates earned badges from how to earn the rest", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        Response.json({
          badges: [
            {
              code: "FOUNDING",
              title: "Founding Donor",
              rule: "Give before launch",
              perk: "Founders events",
              earned: true,
              earned_at: "2026-10-01T12:00:00Z",
              city: null,
              display_title: "Founding Donor",
            },
            {
              code: "CITY50",
              title: "Top 50 City",
              rule: "Rank in the top 50 in your city",
              perk: "",
              earned: true,
              earned_at: "2026-10-02T12:00:00Z",
              city: "Chicago",
              display_title: "Top 50 in Chicago",
            },
            {
              code: "STREAK_30",
              title: "30-day streak",
              rule: "Give for 30 days in a row",
              perk: "",
              earned: false,
              earned_at: null,
              city: null,
              display_title: "30-day streak",
            },
          ],
        }),
      ),
    ),
  );
  render(<Badges />);
  const earned = (await screen.findByRole("heading", { name: "Earned" })).closest("section");
  const unearned = screen.getByRole("heading", { name: "Still to earn" }).closest("section");
  if (!earned || !unearned) throw new Error("Missing sections");
  expect(within(earned).getByText("Founding Donor")).toBeVisible();
  expect(within(earned).getByText("Founders events")).toBeVisible();
  expect(within(earned).getByText("Top 50 in Chicago")).toBeVisible();
  expect(within(earned).queryByText("Top 50 City")).not.toBeInTheDocument();
  expect(within(unearned).getByText("Give for 30 days in a row")).toBeVisible();
  expect(within(earned).queryByText("30-day streak")).not.toBeInTheDocument();
});
