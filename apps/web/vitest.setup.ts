import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Testing Library only auto-cleans when vitest runs with globals enabled, which this
// project does not. Without it, renders leak between tests and assertions can pass
// against a previous test's DOM.
afterEach(cleanup);

// jsdom's Blob and File predate `arrayBuffer`, which every browser this ships to has had
// for years. Polyfilled here rather than avoided in the code, because reading a captured
// photograph is exactly what the code under test has to do.
for (const constructor of [Blob, File]) {
  if (typeof constructor.prototype.arrayBuffer !== "function") {
    Object.defineProperty(constructor.prototype, "arrayBuffer", {
      configurable: true,
      value(this: Blob) {
        return new Promise<ArrayBuffer>((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(reader.result as ArrayBuffer);
          reader.onerror = () => reject(reader.error);
          reader.readAsArrayBuffer(this);
        });
      },
    });
  }
}
