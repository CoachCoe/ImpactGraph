import "fake-indexeddb/auto";
import { beforeEach, describe, expect, it } from "vitest";
import { all, asBlob, put, remove, summarise, type Capture } from "./capture-queue";

function capture(overrides: Partial<Capture> = {}): Capture {
  return {
    id: crypto.randomUUID(),
    projectId: "project-water-12",
    filename: "delivery.jpg",
    mimeType: "image/jpeg",
    bytes: new Uint8Array([0xff, 0xd8, 0xff, 0xd9]).buffer,
    capturedAt: "2026-09-14T09:00:00.000Z",
    hasLocation: true,
    state: "queued",
    attempts: 0,
    ...overrides,
  };
}

describe("the capture queue", () => {
  beforeEach(async () => {
    for (const item of await all()) await remove(item.id);
  });

  it("keeps a photograph taken with no signal", async () => {
    await put(capture({ id: "one" }));
    const queued = await all();
    expect(queued).toHaveLength(1);
    expect(queued[0].state).toBe("queued");
  });

  it("keeps the bytes, not a promise to find them again", async () => {
    /* The phone may be killed between capture and upload, so the image itself has to be
       in the store rather than a handle to a file the browser will not reopen. */
    await put(capture({ id: "bytes" }));
    const [stored] = await all();
    expect(stored.bytes.byteLength).toBe(4);
    expect(asBlob(stored).type).toBe("image/jpeg");
  });

  it("survives the page being thrown away", async () => {
    await put(capture({ id: "survivor" }));
    // A fresh read is what a reload does: nothing is held in memory between them.
    const { all: readAgain } = await import("./capture-queue");
    expect((await readAgain()).map((item) => item.id)).toContain("survivor");
  });

  it("returns captures in the order they were taken", async () => {
    await put(capture({ id: "second", capturedAt: "2026-09-14T10:00:00.000Z" }));
    await put(capture({ id: "first", capturedAt: "2026-09-14T09:00:00.000Z" }));
    expect((await all()).map((item) => item.id)).toEqual(["first", "second"]);
  });

  it("forgets a capture once it has been delivered", async () => {
    await put(capture({ id: "done" }));
    await remove("done");
    expect(await all()).toHaveLength(0);
  });
});

describe("what the operator is told", () => {
  it("never counts a queued capture as delivered", () => {
    /* An operator who believes evidence is filed when it is sitting on their phone will
       stop looking for it. */
    const summary = summarise([
      capture({ state: "queued" }),
      capture({ state: "queued" }),
      capture({ state: "uploading" }),
      capture({ state: "uploaded" }),
      capture({ state: "failed" }),
    ]);
    expect(summary).toEqual({
      onThisDevice: 2,
      uploading: 1,
      delivered: 1,
      failed: 1,
    });
  });

  it("says nothing is delivered when nothing has been", () => {
    expect(summarise([capture(), capture()]).delivered).toBe(0);
  });
});
