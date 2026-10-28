import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, expect } from "vitest";
import { resetAuthForTests } from "@/store/auth";
import { session } from "@/lib/session";

// Every test starts as a fresh page load that has finished checking its
// session: anonymous, nothing stored, no QueryClient bound from a previous test.
// A test about hydration puts the store back to "hydrating" itself.
beforeEach(() => {
  session.bindQueryClient(null);
  resetAuthForTests("ready");
});

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

// jsdom has no matchMedia, which Toaster from sonner needs.
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => true,
  }),
});

expect.extend({});
