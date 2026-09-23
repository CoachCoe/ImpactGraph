import { hasLocation, readApp1, withApp1 } from "@/lib/exif";

/**
 * Shrinking a photograph for a metered connection, without losing why it was taken.
 *
 * A phone camera produces several megabytes. On the 3G connection this feature exists for
 * that is minutes per photograph, and an operator standing at a borehole will give up
 * before it finishes. Re-encoding at a smaller edge and a lower quality brings it to a few
 * hundred kilobytes.
 *
 * The compressed bytes are what is uploaded and therefore what the server hashes, so this
 * runs before the commitment exists rather than after it — ADR-011. Nothing here computes
 * a hash: the client's job is delivery.
 */

/**
 * The budget this is tuned to.
 *
 * A "Regular 3G" profile is about 400 kbit/s up, so every 50 KB is a second of an
 * operator standing still. A 12-megapixel phone photograph is 3-5 MB, which is a minute
 * or more each and several minutes for one delivery -- long enough that people stop
 * bothering, which is the actual failure.
 *
 * 1600px on the long edge at quality 0.72 is chosen to keep a delivery photograph legible
 * enough to read a meter or a serial number while staying in the low hundreds of
 * kilobytes.
 *
 * The budget below is arithmetic, not a measurement: 300 KB at 400 kbit/s is about six
 * seconds, and fifteen leaves room for the request either side of it. Nobody has yet put
 * a real phone on a throttled connection and timed this, and the number should be
 * replaced by one that came from doing so. Raising the edge to 2048 roughly doubles the
 * bytes and therefore the wait.
 */
export const MAX_EDGE = 1600;
export const QUALITY = 0.72;

/** What the round trip is expected to cost on a Regular 3G profile, per photograph. */
export const BUDGET_SECONDS_PER_PHOTOGRAPH = 15;

export type Compressed = {
  bytes: ArrayBuffer;
  mimeType: string;
  /** Whether the coordinates survived. Reported so the operator is told rather than
   *  left to assume, and so a device that never recorded any can say so. */
  hasLocation: boolean;
  originalBytes: number;
};

/** Anything that is not a JPEG is passed through untouched. */
export async function compressForUpload(file: File): Promise<Compressed> {
  const original = new Uint8Array(await file.arrayBuffer());
  if (file.type !== "image/jpeg") {
    return {
      bytes: original.buffer as ArrayBuffer,
      mimeType: file.type,
      hasLocation: false,
      originalBytes: original.byteLength,
    };
  }

  const app1 = readApp1(original);
  const shrunk = await reencode(file);
  if (!shrunk) {
    // No canvas, or the browser refused the image. Sending the original is worse for the
    // connection and correct for the evidence, which is the right way round to fail.
    return {
      bytes: original.buffer as ArrayBuffer,
      mimeType: "image/jpeg",
      hasLocation: hasLocation(app1),
      originalBytes: original.byteLength,
    };
  }

  const carried = withApp1(shrunk, app1);
  return {
    bytes: carried.buffer.slice(
      carried.byteOffset,
      carried.byteOffset + carried.byteLength,
    ) as ArrayBuffer,
    mimeType: "image/jpeg",
    hasLocation: hasLocation(readApp1(carried)),
    originalBytes: original.byteLength,
  };
}

async function reencode(file: File): Promise<Uint8Array | null> {
  if (
    typeof createImageBitmap !== "function" ||
    typeof document === "undefined"
  )
    return null;
  try {
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, MAX_EDGE / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    const context = canvas.getContext("2d");
    if (!context) return null;
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    bitmap.close();
    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, "image/jpeg", QUALITY),
    );
    return blob ? new Uint8Array(await blob.arrayBuffer()) : null;
  } catch {
    return null;
  }
}
