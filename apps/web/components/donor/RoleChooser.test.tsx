import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { analyticsChoice } from "@/lib/analytics";
import { RoleChooser } from "./RoleChooser";

afterEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  vi.unstubAllGlobals();
});

it("records the role choice and leads each role to its own start", () => {
  const fetch = vi.fn().mockResolvedValue(new Response("{}"));
  vi.stubGlobal("fetch", fetch);
  analyticsChoice(true);
  render(<RoleChooser />);
  const donor = screen.getByRole("link", { name: "I’m a donor" });
  const partner = screen.getByRole("link", { name: "I’m a partner" });
  expect(donor.getAttribute("href")).toBe("/donate");
  expect(partner.getAttribute("href")).toBe("/partners");
  fireEvent.click(partner);
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(JSON.parse(fetch.mock.calls[0][1].body).events[0]).toMatchObject({ name: "role_selected", properties: {} });
});
