import "fake-indexeddb/auto";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { all, put, remove } from "@/lib/capture-queue";

const api = vi.fn();
vi.mock("@/lib/api", () => ({ api: (...args: unknown[]) => api(...args) }));

const { FieldCapture } = await import("./FieldCapture");

function photograph(name = "delivery.jpg"): File {
  return new File([new Uint8Array([0xff, 0xd8, 0xff, 0xda, 0x00, 0x02])], name, {
    type: "image/jpeg",
  });
}

async function clear() {
  for (const item of await all()) await remove(item.id);
}

describe("capturing in the field", () => {
  beforeEach(async () => {
    await clear();
    api.mockReset();
    api.mockResolvedValue({});
    vi.stubGlobal("navigator", { onLine: true });
  });

  it("files a photograph the operator took", async () => {
    render(<FieldCapture projectId="project-water-12" />);
    fireEvent.change(screen.getByLabelText("Take a photograph"), {
      target: { files: [photograph()] },
    });
    await waitFor(() => expect(api).toHaveBeenCalled());
    const [path, options] = api.mock.calls[0];
    expect(path).toBe("/evidence");
    expect((options.body as FormData).get("project_id")).toBe("project-water-12");
    // A photograph at a delivery very likely has someone in it, so the declaration
    // defaults the careful way rather than leaving it to be remembered.
    expect((options.body as FormData).get("personal_data")).toBe("true");
  });

  it("keeps a photograph taken with no connection, and says it is not filed", async () => {
    vi.stubGlobal("navigator", { onLine: false });
    render(<FieldCapture projectId="project-water-12" />);
    fireEvent.change(screen.getByLabelText("Take a photograph"), {
      target: { files: [photograph()] },
    });

    await waitFor(async () => expect(await all()).toHaveLength(1));
    expect(api).not.toHaveBeenCalled();
    expect(await screen.findByText(/on this phone and not yet filed/)).toBeInTheDocument();
    expect(screen.getByText(/No connection/)).toBeInTheDocument();
  });

  it("never describes something still on the phone as filed", async () => {
    /* An operator who believes evidence is filed when it is sitting in a queue will stop
       looking for it. */
    vi.stubGlobal("navigator", { onLine: false });
    render(<FieldCapture projectId="project-water-12" />);
    fireEvent.change(screen.getByLabelText("Take a photograph"), {
      target: { files: [photograph()] },
    });
    expect(await screen.findByText("On this phone")).toBeInTheDocument();
    expect(screen.queryByText("Filed")).not.toBeInTheDocument();
  });

  it("shows work left over from a previous session", async () => {
    await put({
      id: "from-yesterday",
      projectId: "project-water-12",
      filename: "borehole.jpg",
      mimeType: "image/jpeg",
      bytes: new Uint8Array([1, 2]).buffer,
      capturedAt: "2026-09-13T16:00:00.000Z",
      hasLocation: true,
      state: "queued",
      attempts: 0,
    });
    vi.stubGlobal("navigator", { onLine: false });
    render(<FieldCapture projectId="project-water-12" />);
    expect(await screen.findByText("borehole.jpg")).toBeInTheDocument();
  });

  it("says whether the location survived, rather than leaving it to be assumed", async () => {
    vi.stubGlobal("navigator", { onLine: false });
    render(<FieldCapture projectId="project-water-12" />);
    fireEvent.change(screen.getByLabelText("Take a photograph"), {
      target: { files: [photograph()] },
    });
    expect(await screen.findByText(/no location/)).toBeInTheDocument();
  });

  it("takes several photographs of one delivery at once", async () => {
    vi.stubGlobal("navigator", { onLine: false });
    render(<FieldCapture projectId="project-water-12" />);
    fireEvent.change(screen.getByLabelText("Take a photograph"), {
      target: { files: [photograph("one.jpg"), photograph("two.jpg")] },
    });
    await waitFor(async () => expect(await all()).toHaveLength(2));
  });

  it("reports a failed upload instead of losing it quietly", async () => {
    api.mockRejectedValue(new Error("Network request failed"));
    render(<FieldCapture projectId="project-water-12" />);
    fireEvent.change(screen.getByLabelText("Take a photograph"), {
      target: { files: [photograph()] },
    });
    expect(await screen.findByText("Not sent")).toBeInTheDocument();
    expect(screen.getByText(/Network request failed/)).toBeInTheDocument();
    expect(await all()).toHaveLength(1);
  });
});
