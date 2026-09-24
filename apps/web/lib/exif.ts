/**
 * Carrying the EXIF segment across a re-encode.
 *
 * A photograph is compressed before upload so it arrives over a metered connection at a
 * sane size. Canvas re-encoding discards every metadata segment, which would silently
 * destroy the coordinates ADR-012 decided to keep — the corroboration would be gone and
 * nothing would say so.
 *
 * So the original's APP1 segment is lifted out and spliced into the compressed JPEG. This
 * is deliberately the only metadata carried across: APP1 is where EXIF lives, and copying
 * segments wholesale would drag along whatever else a camera decided to write.
 */

const MARKER = 0xff;
const SOI = 0xd8;
const APP1 = 0xe1;
const SOS = 0xda;

/** The EXIF segment of a JPEG, including its marker and length, or null if it has none. */
export function readApp1(jpeg: Uint8Array): Uint8Array | null {
  if (jpeg.length < 4 || jpeg[0] !== MARKER || jpeg[1] !== SOI) return null;
  let offset = 2;
  while (offset + 4 <= jpeg.length) {
    if (jpeg[offset] !== MARKER) return null;
    const marker = jpeg[offset + 1];
    // Image data starts here; anything after this point is not a metadata segment.
    if (marker === SOS) return null;
    const length = (jpeg[offset + 2] << 8) | jpeg[offset + 3];
    if (length < 2) return null;
    // A declared length past the end of the buffer is a truncated or hostile image.
    // Returning the clamped slice would splice a malformed segment into the upload.
    if (offset + 2 + length > jpeg.length) return null;
    if (marker === APP1) return jpeg.subarray(offset, offset + 2 + length);
    offset += 2 + length;
  }
  return null;
}

/** The same JPEG with any existing APP1 removed, so splicing cannot leave two. */
export function withoutApp1(jpeg: Uint8Array): Uint8Array {
  const existing = readApp1(jpeg);
  const at = findApp1Offset(jpeg);
  if (!existing || at === null) return jpeg;
  const out = new Uint8Array(jpeg.length - existing.length);
  out.set(jpeg.subarray(0, at), 0);
  out.set(jpeg.subarray(at + existing.length), at);
  return out;
}

function findApp1Offset(jpeg: Uint8Array): number | null {
  let offset = 2;
  while (offset + 4 <= jpeg.length) {
    if (jpeg[offset] !== MARKER) return null;
    const marker = jpeg[offset + 1];
    if (marker === SOS) return null;
    const length = (jpeg[offset + 2] << 8) | jpeg[offset + 3];
    if (length < 2 || offset + 2 + length > jpeg.length) return null;
    if (marker === APP1) return offset;
    offset += 2 + length;
  }
  return null;
}

/** Put an EXIF segment into a JPEG that has none, immediately after the start marker. */
export function withApp1(jpeg: Uint8Array, app1: Uint8Array | null): Uint8Array {
  if (!app1 || app1.length === 0) return jpeg;
  const base = withoutApp1(jpeg);
  const out = new Uint8Array(base.length + app1.length);
  out.set(base.subarray(0, 2), 0);
  out.set(app1, 2);
  out.set(base.subarray(2), 2 + app1.length);
  return out;
}

/**
 * Whether the segment carries a GPS block.
 *
 * Reading the IFD0 entries for the GPS pointer tag rather than parsing all of EXIF: the
 * only question here is whether the coordinates survived, and the operator is told which
 * answer it was rather than being left to assume.
 */
export function hasLocation(app1: Uint8Array | null): boolean {
  if (!app1 || app1.length < 20) return false;
  // "Exif\0\0" then the TIFF header, whose byte order the rest is read in.
  const tiff = 10;
  const little = app1[tiff] === 0x49 && app1[tiff + 1] === 0x49;
  const u16 = (at: number) =>
    little ? app1[at] | (app1[at + 1] << 8) : (app1[at] << 8) | app1[at + 1];
  const u32 = (at: number) =>
    little
      ? app1[at] | (app1[at + 1] << 8) | (app1[at + 2] << 16) | (app1[at + 3] << 24)
      : // Unsigned: a big-endian offset with the high bit set is otherwise negative, and
        // the bounds check below would be passing for the wrong reason.
        ((app1[at] << 24) | (app1[at + 1] << 16) | (app1[at + 2] << 8) | app1[at + 3]) >>> 0;

  const ifd0 = tiff + u32(tiff + 4);
  if (ifd0 + 2 > app1.length) return false;
  const entries = u16(ifd0);
  for (let index = 0; index < entries; index += 1) {
    const entry = ifd0 + 2 + index * 12;
    if (entry + 12 > app1.length) return false;
    if (u16(entry) === 0x8825) return true; // GPS IFD pointer
  }
  return false;
}
