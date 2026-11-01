import { render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import Events, { placeSummary } from "./Events";

afterEach(() => vi.unstubAllGlobals());

const base = {
  id: "e-1",
  slug: "beach",
  title: "Beach cleanup",
  type: "VOLUNTEER" as const,
  description: "",
  venue: "Ocean Beach",
  address: "",
  city: "San Francisco",
  starts_at: "2099-11-07T17:00:00Z",
  ends_at: "2099-11-07T19:00:00Z",
  capacity: 0,
  spots_left: null,
  reservation_status: null,
  invitation_note: null,
  report_issue_url: null,
  cancelled: false,
  booking_opens_at: null,
  early_booking: false,
  time_zone: "America/Los_Angeles",
  checked_in_at: null,
};

it("summarizes the donor's place or the room left", () => {
  expect(placeSummary(base)).toBe("Open to everyone");
  expect(placeSummary({ ...base, spots_left: 1 })).toBe("1 spot left");
  expect(placeSummary({ ...base, spots_left: 0 })).toBe("Full — join the waitlist");
  expect(placeSummary({ ...base, reservation_status: "WAITLISTED" })).toBe("You are on the waitlist");
  expect(placeSummary({ ...base, reservation_status: "RESERVED", cancelled: true })).toBe("Cancelled");
});

it("lists invitations ahead of events near the donor", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) =>
      Promise.resolve(
        Response.json({
          events: String(input).endsWith("/invite-only")
            ? [
                {
                  ...base,
                  id: "e-2",
                  title: "Founders dinner",
                  type: "INVITE_ONLY",
                  invitation_note: "Invitation earned at Founding Donor",
                },
              ]
            : [{ ...base, reservation_status: "RESERVED" }],
        }),
      ),
    ),
  );
  render(<Events />);
  expect(await screen.findByRole("link", { name: "Founders dinner" })).toHaveAttribute("href", "/events/e-2");
  expect(screen.getByText("Invitation earned at Founding Donor")).toBeVisible();
  expect(screen.getByText(/You have a spot/)).toBeVisible();
  const headings = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
  expect(headings).toEqual(["You’re invited", "Coming up near you"]);
});
