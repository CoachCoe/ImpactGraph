import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Rules } from "./Rules";

afterEach(() => vi.unstubAllGlobals());

const rules = {
  badges: [
    { code: "CITY50", signal: "city_rank", threshold: 50, min_population: 100, artwork: "" },
    { code: "7", signal: "streak_days", threshold: 7, min_population: 1, artwork: "" },
  ],
  milestones: [7, 30],
};

it("saves a rank badge's minimum population and leaves other badges at one", async () => {
  const fetch = vi
    .fn()
    .mockImplementation(async (_url: string, init?: RequestInit) =>
      init?.method === "POST" ? Response.json({ saved: true }) : Response.json(rules),
    );
  vi.stubGlobal("fetch", fetch);
  render(<Rules />);
  const population = await screen.findAllByLabelText("Fewest ranked donors before this badge is awarded");
  expect(population).toHaveLength(1);
  fireEvent.change(population[0], { target: { value: "250" } });
  fireEvent.click(screen.getByRole("button", { name: "Save earning rules and artwork" }));
  expect(await screen.findByText("Rules and artwork saved.")).toBeTruthy();
  const [, init] = fetch.mock.calls.find(([, i]) => i?.method === "POST") ?? [];
  expect(JSON.parse(init.body).badges).toEqual([
    { code: "CITY50", signal: "city_rank", threshold: 50, min_population: 250, artwork: "" },
    { code: "7", signal: "streak_days", threshold: 7, min_population: 1, artwork: "" },
  ]);
});
