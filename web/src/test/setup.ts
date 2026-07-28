import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, expect } from "vitest";

// Nothing in the suite may reach the network. Any component or mock that tries
// to fetch is a defect, so fail loudly instead of hanging or hitting a real API.
beforeEach(() => {
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    throw new Error(
      `Unexpected network call in tests: ${String(
        typeof input === "string" || input instanceof URL ? input : input.url,
      )}. Stub it explicitly in the test.`,
    );
  }) as typeof fetch;
});

afterEach(() => {
  cleanup();
});

// jsdom has no ResizeObserver, which recharts' ResponsiveContainer needs.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver ??= ResizeObserverStub as unknown as typeof ResizeObserver;

expect.extend({});
