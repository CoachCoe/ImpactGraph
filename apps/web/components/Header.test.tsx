import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/",
}));

type Fixture = { role: string; displayName: string; organization: { name: string } };
const asRole = (role: string): Fixture => ({
  role,
  displayName: "Ada Mwangi",
  organization: { name: "Global Water Initiative" },
});
const session = { current: null as null | Fixture };
vi.mock("@/components/SessionProvider", () => ({
  useSession: () => ({ session: session.current, loading: false, signOut: vi.fn() }),
}));

import { Header } from "./Header";

/**
 * The header is one set of destinations shown two ways. Below 800px the stylesheet
 * hid the navigation and put nothing in its place, which took /about out of reach on
 * a phone and stranded operators and verifiers on whichever screen they landed on.
 *
 * jsdom applies no stylesheet, so these assert the part that carries the meaning --
 * what the button announces and what it controls -- and the browser suite asserts
 * that the panel is the thing a reader can actually see and tap.
 */
describe("Header", () => {
  it("offers every destination the reader's role allows", () => {
    session.current = null;
    const { unmount } = render(<Header />);
    const nav = screen.getByRole("navigation", { name: /primary/i });
    for (const name of ["Donor", "Money trail", "How it works"]) {
      expect(screen.getByRole("link", { name })).toBeInTheDocument();
    }
    expect(screen.queryByRole("link", { name: "Operator" })).not.toBeInTheDocument();
    unmount();

    session.current = asRole("OPERATOR");
    render(<Header />);
    expect(screen.getByRole("link", { name: "Operator" })).toBeInTheDocument();
    expect(nav).toBeTruthy();
  });

  it("announces whether the panel is open, and says what it controls", () => {
    session.current = null;
    render(<Header />);
    const button = screen.getByRole("button", { name: "Menu" });
    const panel = document.getElementById(button.getAttribute("aria-controls") ?? "");

    expect(panel, "aria-controls must point at an element that exists").not.toBeNull();
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(panel).toHaveAttribute("data-open", "false");

    fireEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(panel).toHaveAttribute("data-open", "true");

    fireEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "false");
  });

  it("closes on Escape and hands focus back to the button", () => {
    session.current = null;
    render(<Header />);
    const button = screen.getByRole("button", { name: "Menu" });

    fireEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");

    // Focus is somewhere inside the panel by the time Escape is pressed.
    screen.getByRole("link", { name: "Money trail" }).focus();
    fireEvent.keyDown(document, { key: "Escape" });

    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(document.activeElement, "focus must not be abandoned off-screen").toBe(button);
  });

  it("is a button, so it is reachable and operable from the keyboard", () => {
    session.current = null;
    render(<Header />);
    const button = screen.getByRole("button", { name: "Menu" });
    expect(button.tagName).toBe("BUTTON");
    // type=button: inside no form here, but the default of "submit" is a trap waiting
    // for the day this markup moves.
    expect(button).toHaveAttribute("type", "button");
  });
});
