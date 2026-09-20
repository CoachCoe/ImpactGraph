import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { IntegrityCheck } from "./IntegrityCheck";

function stubFetch(body: unknown, ok = true) {
  const mock = vi.fn().mockResolvedValue({ ok, json: () => Promise.resolve(body) });
  vi.stubGlobal("fetch", mock);
  return mock;
}

describe("IntegrityCheck", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows nothing until the check is actually run", () => {
    stubFetch({});
    render(<IntegrityCheck evidenceId="ev-1" />);
    // A badge asserting integrity without asking the backend would prove nothing.
    expect(screen.queryByText(/match/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Check this yourself/ })).toBeInTheDocument();
  });

  it("reports the backend's MATCH result and both hashes", async () => {
    stubFetch({
      evidenceId: "ev-1",
      status: "MATCH",
      expected: "sha256:" + "a".repeat(64),
      current: "sha256:" + "a".repeat(64),
      byteCount: 1432,
      explanation: "The stored evidence is byte-for-byte consistent with its commitment.",
    });
    render(<IntegrityCheck evidenceId="ev-1" />);
    fireEvent.click(screen.getByRole("button", { name: /Check this yourself/ }));
    // The pill is gone on the passing path; the seal carries the verdict, and only
    // once the comparison has actually settled.
    // The seal only takes its role once the sweep settles, which runs for real
    // requestAnimationFrame time and can outlast findBy's one-second default.
    expect(
      await screen.findByRole(
        "img",
        { name: /Byte-for-byte match, checked by you just now/ },
        { timeout: 5000 },
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(/byte-for-byte consistent/)).toBeInTheDocument();
  });

  it("reports a MISMATCH rather than softening it", async () => {
    stubFetch({
      evidenceId: "ev-1",
      status: "MISMATCH",
      expected: "sha256:" + "a".repeat(64),
      current: "sha256:" + "b".repeat(64),
      byteCount: 1432,
      explanation: "The stored evidence no longer hashes to its registered commitment.",
    });
    render(<IntegrityCheck evidenceId="ev-1" />);
    fireEvent.click(screen.getByRole("button", { name: /Check this yourself/ }));
    expect(await screen.findByText("Mismatch")).toBeInTheDocument();
    // The hash renders as per-character spans for the comparison, so assert the string
    // assistive technology is given -- which must still be the whole hash.
    expect(screen.getByLabelText("sha256:" + "b".repeat(64))).toBeInTheDocument();
    expect(screen.queryByText("Byte-for-byte match")).not.toBeInTheDocument();
    expect(screen.queryByRole("img", { name: /Byte-for-byte match/ })).not.toBeInTheDocument();
  });

  it("leads with the consequence, in words a reader could repeat", async () => {
    stubFetch({
      evidenceId: "ev-1",
      status: "MATCH",
      expected: "sha256:" + "a".repeat(64),
      current: "sha256:" + "a".repeat(64),
      byteCount: 1432,
      explanation: "It does not prove the document states the truth.",
    });
    render(<IntegrityCheck evidenceId="ev-1" documentType="invoice" />);
    fireEvent.click(screen.getByRole("button", { name: /Check this yourself/ }));
    const headline = await screen.findByRole("heading", { level: 3 });
    expect(headline).toHaveTextContent("Nothing in this invoice has changed since it was registered.");
    // The sentence may describe bytes, documents and change. It may never describe the
    // document as genuine, authentic or true -- that is what the caveat exists to prevent.
    expect(headline.textContent).not.toMatch(/genuine|authentic|true|verified/i);
    // The caveat keeps its place rather than being replaced by the sentence.
    expect(screen.getByText(/does not prove the document states the truth/)).toBeInTheDocument();
  });

  it("states a mismatch plainly, with no softening and no document type invented", async () => {
    stubFetch({
      evidenceId: "ev-1",
      status: "MISMATCH",
      expected: "sha256:" + "a".repeat(64),
      current: "sha256:" + "b".repeat(64),
      byteCount: 1432,
      explanation: "The divergence is detectable.",
    });
    render(<IntegrityCheck evidenceId="ev-1" />);
    fireEvent.click(screen.getByRole("button", { name: /Check this yourself/ }));
    const headline = await screen.findByRole("heading", { level: 3 });
    expect(headline).toHaveTextContent("This document is not the one that was registered.");
  });

  it("sweeps only after the response resolves, never on a timer", async () => {
    // The hazard: choreography driven by a timer animates a claim that has not been
    // checked. Nothing may settle while the request is still in flight.
    let release!: (value: unknown) => void;
    const pending = new Promise((resolve) => {
      release = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() => pending.then(() => ({
        ok: true,
        json: () => Promise.resolve({
          evidenceId: "ev-1",
          status: "MATCH",
          expected: "sha256:" + "a".repeat(64),
          current: "sha256:" + "a".repeat(64),
          byteCount: 1432,
          explanation: "ok",
        }),
      }))),
    );
    const { container } = render(<IntegrityCheck evidenceId="ev-1" />);
    fireEvent.click(screen.getByRole("button", { name: /Check this yourself/ }));
    await waitFor(() =>
      expect(screen.getByRole("button")).toHaveTextContent(/Re-reading/),
    );
    expect(container.querySelectorAll("code span.agrees")).toHaveLength(0);
    expect(screen.queryByText("identical")).not.toBeInTheDocument();

    release(null);
    // The sweep runs for SWEEP_MS of real requestAnimationFrame time, which can exceed
    // waitFor's one-second default once the whole suite is competing for the event loop.
    await waitFor(() => expect(screen.getByText("identical")).toBeInTheDocument(), {
      timeout: 5000,
    });
  });

  it("does not sweep or settle a mismatch", async () => {
    stubFetch({
      evidenceId: "ev-1",
      status: "MISMATCH",
      expected: "sha256:" + "a".repeat(64),
      current: "sha256:" + "b".repeat(64),
      byteCount: 1432,
      explanation: "The divergence is detectable.",
    });
    const { container } = render(<IntegrityCheck evidenceId="ev-1" />);
    fireEvent.click(screen.getByRole("button", { name: /Check this yourself/ }));
    await screen.findByText("Mismatch");
    // A failed check is the product working. It is not a moment to draw out, and there
    // is nothing here to call identical.
    expect(screen.queryByText("identical")).not.toBeInTheDocument();
    expect(container.querySelectorAll("code span.agrees")).toHaveLength(0);
  });

  it("never marks a differing character as agreeing", async () => {
    // Only the last character differs. The sweep must not colour it whatever it passes.
    const expected = "sha256:" + "a".repeat(64);
    stubFetch({
      evidenceId: "ev-1",
      status: "MISMATCH",
      expected,
      current: expected.slice(0, -1) + "b",
      byteCount: 1432,
      explanation: "Divergent.",
    });
    const { container } = render(<IntegrityCheck evidenceId="ev-1" />);
    fireEvent.click(screen.getByRole("button", { name: /Check this yourself/ }));
    await screen.findByText("Mismatch");
    const codes = container.querySelectorAll("code");
    const last = codes[1].querySelectorAll("span");
    expect(last[last.length - 1]).not.toHaveClass("agrees");
  });

  it("reports the byte count the API measured rather than inventing one", async () => {
    stubFetch({
      evidenceId: "ev-1",
      status: "MATCH",
      expected: "sha256:" + "a".repeat(64),
      current: "sha256:" + "a".repeat(64),
      byteCount: 2048,
      explanation: "ok",
    });
    render(<IntegrityCheck evidenceId="ev-1" />);
    fireEvent.click(screen.getByRole("button", { name: /Check this yourself/ }));
    expect(await screen.findByText(/2,048 bytes/)).toBeInTheDocument();
  });

  it("settles in one step when the reader prefers reduced motion", async () => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn() }),
    );
    stubFetch({
      evidenceId: "ev-1",
      status: "MATCH",
      expected: "sha256:" + "a".repeat(64),
      current: "sha256:" + "a".repeat(64),
      byteCount: 1432,
      explanation: "ok",
    });
    render(<IntegrityCheck evidenceId="ev-1" />);
    fireEvent.click(screen.getByRole("button", { name: /Check this yourself/ }));
    // Same settled state, reached without the sweep.
    expect(await screen.findByText("identical")).toBeInTheDocument();
  });

  it("surfaces an error instead of implying the evidence is fine", async () => {
    stubFetch({ detail: { message: "The stored evidence object could not be read back" } }, false);
    render(<IntegrityCheck evidenceId="ev-1" />);
    fireEvent.click(screen.getByRole("button", { name: /Check this yourself/ }));
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.queryByText("Byte-for-byte match")).not.toBeInTheDocument();
    expect(screen.queryByRole("img", { name: /Byte-for-byte match/ })).not.toBeInTheDocument();
  });
});
