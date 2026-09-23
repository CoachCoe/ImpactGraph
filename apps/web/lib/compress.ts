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

export const MAX_EDGE = 1600;
export const QUALITY = 0.72;

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
  if (typeof createImageBitmap !== "function" || typeof document === "undefined") return null;
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
