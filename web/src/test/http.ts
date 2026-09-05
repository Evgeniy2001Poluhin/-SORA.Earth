import { vi } from "vitest";

/**
 * Transport stubs for production-mode tests (#218).
 *
 * `src/test/setup.ts` replaces `fetch` before every test with one that throws,
 * so nothing in the suite can reach the network by accident. A production-mode
 * test has to put a real answer back, and these are the shapes it needs:
 * a body, a status, or a thrown transport error.
 *
 * `numbersIn` lives here rather than in one test file because five files now
 * need it, and the thing it guards -- "a failure must never arrive as a
 * plausible score" -- is exactly the property that must not drift between
 * copies. It was written for `src/api/no-mock-fallback.test.ts` (#216), which
 * imports it from here now.
 */

/** Every finite number anywhere in a value: anything a page could render. */
export function numbersIn(value: unknown): number[] {
  const found: number[] = [];
  const walk = (v: unknown) => {
    if (typeof v === "number" && Number.isFinite(v)) found.push(v);
    else if (Array.isArray(v)) v.forEach(walk);
    else if (v && typeof v === "object") Object.values(v).forEach(walk);
  };
  walk(value);
  return found;
}

/** Numbers in the range a reader would mistake for a score or a percentage. */
export function scoreLikeNumbersIn(value: unknown): number[] {
  return numbersIn(value).filter((n) => n >= 1 && n <= 100);
}

export interface FetchCall {
  url: string;
  method: string;
  body: unknown;
}

/** What the code under test actually asked for, so a test can assert the
 *  request rather than only the response. */
export function callsOf(stub: ReturnType<typeof vi.fn>): FetchCall[] {
  return stub.mock.calls.map(([input, init]) => {
    const request = (init ?? {}) as RequestInit;
    let body: unknown = request.body;
    if (typeof body === "string") {
      try {
        body = JSON.parse(body);
      } catch {
        /* leave it as the raw string */
      }
    }
    return {
      url: String(input),
      method: String(request.method ?? "GET"),
      body,
    };
  });
}

/** A 200 carrying `payload` as JSON. */
export function stubJson(payload: unknown) {
  const stub = vi.fn(async () =>
    new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "content-type": "application/json" },
    }),
  );
  globalThis.fetch = stub as unknown as typeof fetch;
  return stub;
}

/** A non-2xx response. `api()` turns this into a thrown Error naming the
 *  status, and nothing downstream may turn it back into a number. */
export function stubStatus(status: number, body = "upstream said no") {
  const stub = vi.fn(async () => new Response(body, { status }));
  globalThis.fetch = stub as unknown as typeof fetch;
  return stub;
}

/** A transport that never answers: `TypeError` is what a browser throws for a
 *  dropped connection, `AbortError` for a timeout. */
export function stubTransportError(error: Error = new TypeError("Failed to fetch")) {
  const stub = vi.fn(async () => {
    throw error;
  });
  globalThis.fetch = stub as unknown as typeof fetch;
  return stub;
}
