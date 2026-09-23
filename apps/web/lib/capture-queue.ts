/**
 * Photographs captured in the field, held until they reach the server.
 *
 * IndexedDB rather than memory, because the acceptance this exists for is that an
 * operator can capture at a borehole with no signal, put the phone in a pocket, and find
 * the work still there after the browser was killed.
 *
 * The queue holds bytes and delivers them. It does not hash them: the commitment is
 * computed server-side over what actually arrived, and a client that computed its own
 * would be asserting what it sent rather than proving it. That is the one thing an
 * offline queue must not quietly become.
 */

export type QueuedState = "queued" | "uploading" | "uploaded" | "failed";

export type Capture = {
  id: string;
  projectId: string;
  filename: string;
  mimeType: string;
  /** The image itself, as bytes rather than a Blob: mobile Safari has shipped versions
   *  that fail to round-trip a Blob through IndexedDB, and mobile Safari is the device
   *  this whole feature exists for. */
  bytes: ArrayBuffer;
  capturedAt: string;
  /** Whether the coordinates survived compression, so the operator is told rather than
   *  left to assume. See ADR-012. */
  hasLocation: boolean;
  state: QueuedState;
  attempts: number;
  /** Why the last attempt failed, for an operator who has to decide what to do next. */
  error?: string;
};

const DATABASE = "impactgraph-capture";
const STORE = "captures";
const VERSION = 1;

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE, VERSION);
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains(STORE)) {
        database.createObjectStore(STORE, { keyPath: "id" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function transact<T>(
  mode: IDBTransactionMode,
  run: (store: IDBObjectStore) => IDBRequest<T>,
): Promise<T> {
  return open().then(
    (database) =>
      new Promise<T>((resolve, reject) => {
        const transaction = database.transaction(STORE, mode);
        const request = run(transaction.objectStore(STORE));
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
        transaction.oncomplete = () => database.close();
      }),
  );
}

/** The stored bytes as something fetch can send. */
export function asBlob(capture: Capture): Blob {
  return new Blob([capture.bytes], { type: capture.mimeType });
}

export function put(capture: Capture): Promise<unknown> {
  return transact("readwrite", (store) => store.put(capture));
}

export function all(): Promise<Capture[]> {
  return transact<Capture[]>("readonly", (store) => store.getAll()).then((rows) =>
    rows.sort((a, b) => a.capturedAt.localeCompare(b.capturedAt)),
  );
}

export function remove(id: string): Promise<unknown> {
  return transact("readwrite", (store) => store.delete(id));
}

/**
 * What the operator is told, and never more than is true.
 *
 * "Uploaded" means the server acknowledged the bytes, not that this device finished
 * sending them. Everything else is still on the phone and says so, because an operator
 * who believes evidence is filed when it is sitting in a queue will stop looking for it.
 */
export function summarise(captures: readonly Capture[]): {
  onThisDevice: number;
  uploading: number;
  delivered: number;
  failed: number;
} {
  return {
    onThisDevice: captures.filter((item) => item.state === "queued").length,
    uploading: captures.filter((item) => item.state === "uploading").length,
    delivered: captures.filter((item) => item.state === "uploaded").length,
    failed: captures.filter((item) => item.state === "failed").length,
  };
}
