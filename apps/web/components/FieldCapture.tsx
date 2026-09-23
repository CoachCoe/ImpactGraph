"use client";

import { ChangeEvent, useCallback, useEffect, useState } from "react";
import { all, put, summarise, type Capture } from "@/lib/capture-queue";
import { compressForUpload } from "@/lib/compress";
import { drain } from "@/lib/drain";
import { api } from "@/lib/api";

/**
 * Photographing a delivery where it happened, with or without a connection.
 *
 * The queue is the product here. An operator at a borehole on 3G, or on nothing at all,
 * takes the photographs and walks away; they leave the phone when there is signal. What
 * this screen must never do is imply that has already happened.
 */
export function FieldCapture({ projectId }: { projectId: string }) {
  const [captures, setCaptures] = useState<Capture[]>([]);
  const [busy, setBusy] = useState(false);
  const [online, setOnline] = useState(true);

  const refresh = useCallback(async () => setCaptures(await all()), []);

  const send = useCallback(async () => {
    setBusy(true);
    try {
      await drain(async (capture, body) => {
        const form = new FormData();
        form.set("evidence_id", `ev-${capture.id}`);
        form.set("project_id", capture.projectId);
        form.set("evidence_type", "PHOTOGRAPH");
        form.set("visibility", "RESTRICTED");
        // A photograph taken at a delivery is very likely to have someone in it, so the
        // declaration defaults the careful way and the basis is recorded before
        // registration, as it is for any other document.
        form.set("personal_data", "true");
        form.set("file", body, capture.filename);
        await api(`/evidence`, {
          method: "POST",
          headers: { "Idempotency-Key": capture.id },
          body: form,
        });
      });
    } finally {
      setBusy(false);
      await refresh();
    }
  }, [refresh]);

  useEffect(() => {
    void refresh();
    const connected = () => {
      setOnline(true);
      void send();
    };
    const disconnected = () => setOnline(false);
    setOnline(typeof navigator === "undefined" ? true : navigator.onLine);
    window.addEventListener("online", connected);
    window.addEventListener("offline", disconnected);
    // Reconnect fires once; a connection that is merely bad never fires it at all, so the
    // queue is also tried on a slow timer rather than waiting for an event that may not
    // come.
    const timer = window.setInterval(() => void send(), 30_000);
    return () => {
      window.removeEventListener("online", connected);
      window.removeEventListener("offline", disconnected);
      window.clearInterval(timer);
    };
  }, [refresh, send]);

  const capture = async (event: ChangeEvent<HTMLInputElement>) => {
    const chosen = Array.from(event.target.files ?? []);
    event.target.value = "";
    for (const file of chosen) {
      const compressed = await compressForUpload(file);
      await put({
        id: crypto.randomUUID(),
        projectId,
        filename: file.name || "capture.jpg",
        mimeType: compressed.mimeType,
        bytes: compressed.bytes,
        capturedAt: new Date().toISOString(),
        hasLocation: compressed.hasLocation,
        state: "queued",
        attempts: 0,
      });
    }
    await refresh();
    void send();
  };

  const summary = summarise(captures);
  const waiting = summary.onThisDevice + summary.failed;

  return (
    <section className="panel capturePanel">
      <span className="eyebrow">FIELD CAPTURE</span>
      <h2>Photograph a delivery</h2>
      <p className="subtle">
        Works with no signal. Photographs stay on this phone until they reach the server,
        and survive closing the browser.
      </p>

      <label className="captureButton">
        <span>Take a photograph</span>
        <input
          type="file"
          accept="image/*"
          capture="environment"
          multiple
          aria-label="Take a photograph"
          onChange={capture}
        />
      </label>

      <p className={online ? "captureStatus" : "captureStatus offline"} role="status">
        {waiting === 0
          ? "Nothing waiting on this phone."
          : `${waiting} ${waiting === 1 ? "photograph is" : "photographs are"} on this phone and not yet filed.`}
        {online ? "" : " No connection — they will be sent when there is one."}
      </p>

      {captures.length > 0 ? (
        <ul className="captureList">
          {captures.map((item) => (
            <li key={item.id} className={item.state}>
              <span>
                <b>{item.filename}</b>
                <small>
                  {new Date(item.capturedAt).toLocaleTimeString()}
                  {item.hasLocation ? " · location recorded" : " · no location"}
                  {item.error ? ` · ${item.error}` : ""}
                </small>
              </span>
              <em>{LABEL[item.state]}</em>
            </li>
          ))}
        </ul>
      ) : null}

      {waiting > 0 ? (
        <button className="secondary full" onClick={() => void send()} disabled={busy || !online}>
          {busy ? "Sending…" : "Send now"}
        </button>
      ) : null}
    </section>
  );
}

/** Deliberately not "saved" or "done" for anything still on the device. */
const LABEL: Record<Capture["state"], string> = {
  queued: "On this phone",
  uploading: "Sending",
  uploaded: "Filed",
  failed: "Not sent",
};
