import { render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import Network from "./Network";

afterEach(() => vi.unstubAllGlobals());

it("shows the network's annual giving, next milestone, and named invitees", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        Response.json({
          active_count: 3,
          annualized_minor: 474500,
          next_milestone: { label: "Connector", target: 5, remaining: 2 },
          invitees: [
            { name: "Ann B.", active: true },
            { name: "Cy D.", active: false },
          ],
          anonymous_active: 1,
        }),
      ),
    ),
  );
  render(<Network />);
  expect(await screen.findByText("Together your network funds $4,745 a year.")).toBeVisible();
  expect(screen.getByText("2 more to reach Connector (5 active).")).toBeVisible();
  expect(screen.getByText("Cy D. · Not giving")).toBeVisible();
  expect(screen.getByText(/1 more person is giving but chose not to be named/)).toBeVisible();
});
