import { describe, expect, it, vi } from "vitest";
import { compressForUpload } from "./compress";
import { hasLocation, readApp1 } from "./exif";

function segment(marker: number, payload: number[]): number[] {
  const length = payload.length + 2;
  return [0xff, marker, (length >> 8) & 0xff, length & 0xff, ...payload];
}

const GPS_EXIF = segment(0xe1, [
  0x45, 0x78, 0x69, 0x66, 0x00, 0x00,
  0x49, 0x49, 0x2a, 0x00, 0x08, 0x00, 0x00, 0x00,
  0x01, 0x00,
  0x25, 0x88, 0x04, 0x00, 0x01, 0x00, 0x00, 0x00, 0x1a, 0x00, 0x00, 0x00,
]);

function photograph(): File {
  const bytes = new Uint8Array([0xff, 0xd8, ...GPS_EXIF, 0xff, 0xda, 0x00, 0x02]);
  return new File([bytes], "delivery.jpg", { type: "image/jpeg" });
}

describe("preparing a photograph for a metered connection", () => {
  it("keeps the coordinates when the browser can re-encode", async () => {
    // A canvas that produces a JPEG with no metadata, which is what a real one does.
    const shrunk = new Uint8Array([0xff, 0xd8, ...segment(0xe0, [0x4a, 0x46]), 0xff, 0xda, 0x00, 0x02]);
    vi.stubGlobal("createImageBitmap", async () => ({ width: 4000, height: 3000, close() {} }));
    const context = { drawImage: vi.fn() };
    vi.spyOn(document, "createElement").mockReturnValue({
      getContext: () => context,
      toBlob: (done: (b: Blob) => void) => done(new Blob([shrunk], { type: "image/jpeg" })),
    } as unknown as HTMLCanvasElement);

    const result = await compressForUpload(photograph());
    expect(result.hasLocation).toBe(true);
    expect(hasLocation(readApp1(new Uint8Array(result.bytes)))).toBe(true);
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("sends the original rather than losing the evidence when it cannot re-encode", async () => {
    /* Worse for the connection and correct for the evidence, which is the right way
       round to fail. */
    vi.stubGlobal("createImageBitmap", undefined);
    const original = photograph();
    const result = await compressForUpload(original);
    expect(result.bytes.byteLength).toBe(original.size);
    expect(result.hasLocation).toBe(true);
    vi.unstubAllGlobals();
  });

  it("does not claim coordinates a photograph never carried", async () => {
    vi.stubGlobal("createImageBitmap", undefined);
    const plain = new File([new Uint8Array([0xff, 0xd8, 0xff, 0xda, 0x00, 0x02])], "a.jpg", {
      type: "image/jpeg",
    });
    expect((await compressForUpload(plain)).hasLocation).toBe(false);
    vi.unstubAllGlobals();
  });

  it("leaves a document that is not a photograph alone", async () => {
    const invoice = new File([new Uint8Array([1, 2, 3, 4])], "INV.txt", { type: "text/plain" });
    const result = await compressForUpload(invoice);
    expect(result.mimeType).toBe("text/plain");
    expect(result.bytes.byteLength).toBe(4);
  });
});
