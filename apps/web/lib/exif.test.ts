import { describe, expect, it } from "vitest";
import { hasLocation, readApp1, withApp1, withoutApp1 } from "./exif";

/** A JPEG skeleton: start marker, the given segments, then start-of-scan and data. */
function jpeg(...segments: Uint8Array[]): Uint8Array {
  const body = segments.reduce((total, s) => total + s.length, 0);
  const out = new Uint8Array(2 + body + 4);
  out.set([0xff, 0xd8], 0);
  let at = 2;
  for (const segment of segments) {
    out.set(segment, at);
    at += segment.length;
  }
  out.set([0xff, 0xda, 0x00, 0x02], at);
  return out;
}

function segment(marker: number, payload: number[]): Uint8Array {
  const length = payload.length + 2;
  return new Uint8Array([0xff, marker, (length >> 8) & 0xff, length & 0xff, ...payload]);
}

/** An APP1 whose IFD0 carries the GPS pointer tag, little-endian. */
function exifWithGps(): Uint8Array {
  const tiff = [
    0x49, 0x49, 0x2a, 0x00, 0x08, 0x00, 0x00, 0x00, // header, IFD0 at offset 8
    0x01, 0x00, // one entry
    0x25, 0x88, 0x04, 0x00, 0x01, 0x00, 0x00, 0x00, 0x1a, 0x00, 0x00, 0x00, // GPS pointer
  ];
  return segment(0xe1, [0x45, 0x78, 0x69, 0x66, 0x00, 0x00, ...tiff]);
}

function exifWithoutGps(): Uint8Array {
  const tiff = [
    0x49, 0x49, 0x2a, 0x00, 0x08, 0x00, 0x00, 0x00,
    0x01, 0x00,
    0x0f, 0x01, 0x02, 0x00, 0x04, 0x00, 0x00, 0x00, 0x1a, 0x00, 0x00, 0x00, // Make
  ];
  return segment(0xe1, [0x45, 0x78, 0x69, 0x66, 0x00, 0x00, ...tiff]);
}

describe("carrying EXIF across a re-encode", () => {
  it("finds the segment a camera wrote", () => {
    const original = jpeg(segment(0xe0, [0x4a, 0x46]), exifWithGps());
    const found = readApp1(original);
    expect(found).not.toBeNull();
    expect(found![1]).toBe(0xe1);
  });

  it("returns nothing for a JPEG a canvas produced", () => {
    // Which is the whole problem: re-encoding drops it, and nothing says so.
    expect(readApp1(jpeg(segment(0xe0, [0x4a, 0x46])))).toBeNull();
  });

  it("puts the original segment into the compressed image", () => {
    const original = jpeg(exifWithGps());
    const compressed = jpeg(segment(0xe0, [0x4a, 0x46]));
    const carried = withApp1(compressed, readApp1(original));

    expect(readApp1(carried)).not.toBeNull();
    expect(hasLocation(readApp1(carried))).toBe(true);
    // The image data is still there, after the segment rather than instead of it.
    expect(carried.length).toBeGreaterThan(compressed.length);
    expect(Array.from(carried.subarray(0, 2))).toEqual([0xff, 0xd8]);
  });

  it("does not leave two EXIF segments when one is already present", () => {
    const already = jpeg(exifWithoutGps());
    const carried = withApp1(already, readApp1(jpeg(exifWithGps())));
    // The one it kept is the one that was carried across, and there is only one.
    expect(hasLocation(readApp1(carried))).toBe(true);
    expect(readApp1(withoutApp1(carried))).toBeNull();
  });

  it("reports whether the coordinates actually survived", () => {
    // The operator is told which answer it was rather than left to assume.
    expect(hasLocation(readApp1(jpeg(exifWithGps())))).toBe(true);
    expect(hasLocation(readApp1(jpeg(exifWithoutGps())))).toBe(false);
    expect(hasLocation(null)).toBe(false);
  });

  it("does not walk off the end of a truncated or hostile image", () => {
    expect(readApp1(new Uint8Array([0xff, 0xd8]))).toBeNull();
    expect(readApp1(new Uint8Array([0xff, 0xd8, 0xff, 0xe1, 0xff, 0xff]))).toBeNull();
    expect(readApp1(new Uint8Array([0x00, 0x01, 0x02]))).toBeNull();
    expect(hasLocation(new Uint8Array([0xff, 0xe1, 0x00, 0x04]))).toBe(false);
  });

  it("leaves a JPEG alone when there is nothing to carry", () => {
    const compressed = jpeg(segment(0xe0, [0x4a, 0x46]));
    expect(withApp1(compressed, null)).toBe(compressed);
  });
});
