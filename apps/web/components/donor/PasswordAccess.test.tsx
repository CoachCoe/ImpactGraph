import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PasswordAccess } from "./PasswordAccess";

afterEach(() => vi.unstubAllGlobals());

it("tells the donor a wrong current password is wrong, not that they are signed out", async () => {
  const fetch = vi.fn(() =>
    Promise.resolve(
      Response.json(
        { detail: "The email or password is incorrect", code: "CURRENT_PASSWORD_INCORRECT" },
        { status: 422 },
      ),
    ),
  );
  vi.stubGlobal("fetch", fetch);
  render(<PasswordAccess setup />);
  fireEvent.click(screen.getByText("Set or change your password"));
  fireEvent.change(screen.getByLabelText(/Current password/), { target: { value: "typo" } });
  fireEvent.change(screen.getByLabelText("New password"), { target: { value: "a long and unique password" } });
  fireEvent.click(screen.getByRole("button", { name: "Save password" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Your current password is incorrect");
  expect(fetch).toHaveBeenCalledWith("/api/donor/me/password", expect.objectContaining({ method: "POST" }));
});
