import "fake-indexeddb/auto";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { all, put, remove, type Capture } from "./capture-queue";
import { drain } from "./drain";

function capture(id: string): Capture {
  return {
    id,
    projectId: "project-water-12",
    filename: `${id}.jpg`,
    mimeType: "image/jpeg",
    bytes: new Uint8Array([0xff, 0xd8, 0xff, 0xd9]).buffer,
    capturedAt: `2026-09-14T09:0${id.length}:00.000Z`,
    hasLocation: true,
    state: "queued",
    attempts: 0,
  };
}

describe("draining the queue", () => {
  beforeEach(async () => {
    for (const item of await all()) await remove(item.id);
    vi.stubGlobal("navigator", { onLine: true });
  });

  it("delivers what is queued and forgets it only once the server has it", async () => {
    await put(capture("a"));
    await put(capture("bb"));
    const result = await drain(async () => {});
    expect(result).toMatchObject({ delivered: 2, failed: 0, remaining: 0 });
    expect(await all()).toHaveLength(0);
  });

  it("keeps a capture the server did not accept, with the reason", async () => {
    /* Dropping it would be losing evidence somebody walked to a borehole to collect. */
    await put(capture("a"));
    const result = await drain(async () => {
      throw new Error("Network request failed");
    });
    expect(result).toMatchObject({ delivered: 0, failed: 1, remaining: 1 });
    const [kept] = await all();
    expect(kept.state).toBe("failed");
    expect(kept.attempts).toBe(1);
    expect(kept.error).toContain("Network request failed");
  });

  it("stops at the first failure rather than hammering a connection that is down", async () => {
    await put(capture("a"));
    await put(capture("bb"));
    await put(capture("ccc"));
    const attempted: string[] = [];
    await drain(async (item) => {
      attempted.push(item.id);
      throw new Error("offline");
    });
    expect(attempted).toEqual(["a"]);
  });

  it("does nothing at all when the device knows it is offline", async () => {
    vi.stubGlobal("navigator", { onLine: false });
    await put(capture("a"));
    const upload = vi.fn();
    const result = await drain(upload);
    expect(upload).not.toHaveBeenCalled();
    expect(result).toMatchObject({ delivered: 0, remaining: 1 });
  });

  it("can be called again after a failure and picks up where it stopped", async () => {
    await put(capture("a"));
    await drain(async () => {
      throw new Error("offline");
    });
    const result = await drain(async () => {});
    expect(result).toMatchObject({ delivered: 1, remaining: 0 });
  });
});

describe("two drains at once", () => {
  beforeEach(async () => {
    for (const item of await all()) await remove(item.id);
    vi.stubGlobal("navigator", { onLine: true });
  });

  it("uploads a capture once even when capture, reconnect and the timer all ask", async () => {
    /* Relying on the server's idempotency key to cover a race on this side is relying on
       somebody else's property to hold up ours. */
    await put(capture("a"));
    await put(capture("bb"));
    const sent: string[] = [];
    const slowUpload = async (item: { id: string }) => {
      sent.push(item.id);
      await new Promise((resolve) => setTimeout(resolve, 10));
    };

    const [first, second, third] = await Promise.all([
      drain(slowUpload),
      drain(slowUpload),
      drain(slowUpload),
    ]);

    expect(sent.sort()).toEqual(["a", "bb"]);
    expect(first.delivered + second.delivered + third.delivered).toBe(2);
    expect(await all()).toHaveLength(0);
  });
});
