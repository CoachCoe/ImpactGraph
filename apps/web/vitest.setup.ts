import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Testing Library only auto-cleans when vitest runs with globals enabled, which this
// project does not. Without it, renders leak between tests and assertions can pass
// against a previous test's DOM.
afterEach(cleanup);
