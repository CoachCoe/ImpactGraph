import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import EventEditor, { eventInput, instant, localInput } from "./[eventId]/EventEditor";
import Events from "./Events";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const event = {
  id: "e-1",
  slug: "beach-cleanup",
  title: "Beach cleanup",
  type: "VOLUNTEER",
  description: "Bring gloves.",
  venue: "Ocean Beach",
  address: "Great Hwy",
  city: "San Francisco",
  zip: "94121",
  starts_at: "2026-11-07T17:00:00Z",
  ends_at: "2026-11-07T19:00:00Z",
  capacity: 20,
  qualifying_perk: "",
  booking_opens_at: "",
  time_zone: "America/Los_Angeles",
  publish_state: "PUBLISHED",
  cancelled_at: null,
  reserved: 1,
  waitlisted: 1,
  checked_in: 0,
};

function stubApi() {
  const fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (init?.method === "POST") return Promise.resolve(Response.json({ cancelled: true }));
    if (url.includes("?offset=")) return Promise.resolve(Response.json({ events: [event], next_offset: null }));
    return Promise.resolve(
      Response.json({
        ...event,
        attendees: [
          {
            donor_id: "d-1",
            email: "ann@example.org",
            public_name: "Ann",
            status: "RESERVED",
            reserved_at: "2026-10-20T12:00:00Z",
            checked_in_at: null,
          },
          {
            donor_id: "d-2",
            email: "bo@example.org",
            public_name: "",
            status: "WAITLISTED",
            reserved_at: "2026-10-21T12:00:00Z",
            checked_in_at: null,
          },
        ],
      }),
    );
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

it("lists events with their RSVP counts", async () => {
  stubApi();
  render(<Events />);
  expect(await screen.findByRole("link", { name: "Beach cleanup" })).toHaveAttribute("href", "/staff/events/e-1");
  expect(screen.getByText("1 of 20 reserved · 1 waitlisted · 0 checked in")).toBeInTheDocument();
});

it("round-trips times through the browser's zone", () => {
  expect(instant(localInput("2026-11-07T17:00:00Z"))).toBe("2026-11-07T17:00:00Z");
  expect(instant("not a time")).toBeNull();
});

it("validates the editor form", () => {
  const form = new FormData();
  for (const [name, value] of Object.entries({
    slug: "s",
    title: "t",
    type: "COMMUNITY",
    perk: "FOUNDING_EVENTS",
    starts_at: "2026-11-07T10:00",
    ends_at: "2026-11-07T09:00",
    capacity: "0",
  }))
    form.set(name, value);
  expect(eventInput(form)).toBe("The event must end after it starts.");
  form.set("ends_at", "2026-11-07T12:00");
  expect(eventInput(form)).toMatchObject({ capacity: 0, qualifying_perk: "", booking_opens_at: "" });
  form.set("booking_opens_at", "2026-11-07T11:00");
  expect(eventInput(form)).toBe("Booking must open before the event starts.");
  form.set("booking_opens_at", "2026-11-05T09:00");
  expect(eventInput(form)).toMatchObject({ booking_opens_at: instant("2026-11-05T09:00"), time_zone: "" });
  form.set("time_zone", " America/Chicago ");
  expect(eventInput(form)).toMatchObject({ time_zone: "America/Chicago" });
});

it("checks an attendee in by hand and cancels the event", async () => {
  const fetch = stubApi();
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<EventEditor event="e-1" />);
  fireEvent.click(await screen.findByRole("button", { name: "Check in ann@example.org" }));
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("ann@example.org checked in."));
  expect(screen.queryByRole("button", { name: "Check in bo@example.org" })).not.toBeInTheDocument();
  expect(
    fetch.mock.calls.some(([u, i]) => String(u).endsWith("/e-1/attendees/d-1/check-in") && i?.method === "POST"),
  ).toBe(true);

  fireEvent.click(await screen.findByRole("button", { name: "Cancel event" }));
  expect(window.confirm).toHaveBeenCalledWith("Cancel Beach cleanup? 2 people will be notified.");
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Event cancelled."));
  fireEvent.click(await screen.findByRole("button", { name: "Show check-in QR code" }));
  expect(screen.getByRole("img", { name: "Check-in QR code for Beach cleanup" })).toHaveAttribute(
    "src",
    "/api/donor/staff/events/e-1/check-in.svg",
  );
});
