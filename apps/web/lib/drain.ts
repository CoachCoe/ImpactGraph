import { all, asBlob, put, remove, type Capture } from "@/lib/capture-queue";

/**
 * Getting queued photographs to the server, one at a time, whenever there is a connection.
 *
 * One at a time on purpose: an operator on a 3G connection who uploads four photographs in
 * parallel gets four slow uploads rather than one quick one, and a failure part-way leaves
 * a less comprehensible state to explain.
 */

export type Uploader = (capture: Capture, body: Blob) => Promise<void>;

export type DrainResult = {
  delivered: number;
  failed: number;
  remaining: number;
};

/**
 * Deliver what is queued. Safe to call repeatedly and while offline.
 *
 * A capture is removed from the queue only after the server has acknowledged it. A failure
 * leaves it queued with the reason recorded, because the alternative -- dropping it -- is
 * losing evidence somebody walked to a borehole to collect.
 */
//: One drain at a time. Capture, the reconnect listener and the timer all ask for one,
//: and two running together read the same queue, both mark a capture uploading and both
//: upload it. Module-level because the queue is global; component state would also be
//: stale inside the timer's closure.
let draining = false;

export async function drain(upload: Uploader): Promise<DrainResult> {
  if (draining)
    return { delivered: 0, failed: 0, remaining: (await all()).length };
  if (typeof navigator !== "undefined" && navigator.onLine === false) {
    const waiting = await all();
    return { delivered: 0, failed: 0, remaining: waiting.length };
  }

  draining = true;
  let delivered = 0;
  let failed = 0;
  try {
    for (const capture of await all()) {
      await put({ ...capture, state: "uploading" });
      try {
        await upload(capture, asBlob(capture));
        await remove(capture.id);
        delivered += 1;
      } catch (cause) {
        failed += 1;
        await put({
          ...capture,
          state: "failed",
          attempts: capture.attempts + 1,
          error:
            cause instanceof Error
              ? cause.message
              : "The upload did not complete.",
        });
        // Stop at the first failure rather than working through a queue that is going to
        // fail the same way: the connection is the likely cause, and hammering it is not
        // going to help an operator standing in a field.
        break;
      }
    }
  } finally {
    draining = false;
  }
  return { delivered, failed, remaining: (await all()).length };
}
