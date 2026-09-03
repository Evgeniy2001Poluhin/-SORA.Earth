/**
 * A failing API must fail. It must never quietly become a plausible number.
 *
 * `isMock` is a build-time constant, so with `VITE_API_BASE` set the mock
 * branches are dead code and Vite removes them — that is what
 * `Dockerfile.prod`'s post-build grep proves about the shipped bundle. These
 * tests assert the other half: that in production mode nothing *else* supplies
 * a fallback when the network or the server misbehaves.
 *
 * The distinction matters because the failure being guarded against is not a
 * crash. It is a page that looks completely normal while showing numbers the
 * model never produced — which is what production was doing on 2026-09-03, and
 * which no health check can see.
 *
 * ## Why these force production mode explicitly
 *
 * `vitest.config.ts` sets no `VITE_API_BASE`, so the suite runs with
 * `isMock === true` and every endpoint module answers from canned data. Ten of
 * the thirty-one existing tests fail when that is switched off, because they
 * are exercising the mock rather than the code they are named after. Fixing
 * those is real work and belongs in its own change; these tests opt into
 * production mode for themselves instead of flipping it for everyone.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

// Production mode for this file only. Must be hoisted above the imports of the
// modules under test, which read `isMock` at module scope.
vi.mock("./mock", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./mock")>();
  return { ...actual, isMock: false };
});

import { evaluateApi } from "./endpoints/evaluate";
import { reportApi } from "./endpoints/report";
import { isMock } from "./mock";

/** Anything that could be read off a page as a score. */
function numbersIn(value: unknown): number[] {
  const found: number[] = [];
  const walk = (v: unknown) => {
    if (typeof v === "number" && Number.isFinite(v)) found.push(v);
    else if (Array.isArray(v)) v.forEach(walk);
    else if (v && typeof v === "object") Object.values(v).forEach(walk);
  };
  walk(value);
  return found;
}

const BODY = {
  project_name: "Solar Farm",
  country: "Sweden",
  budget_usd: 150000,
  co2_reduction_tons_per_year: 340,
  social_impact_score: 9,
  project_duration_months: 18,
};

describe("the guard on these tests", () => {
  it("really is in production mode", () => {
    // Without this the tests below would pass by talking to the mock, which is
    // the exact opposite of what they claim to check.
    expect(isMock).toBe(false);
  });
});

describe("when the API fails", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("a 500 rejects instead of returning a score", async () => {
    globalThis.fetch = vi.fn(async () =>
      new Response("upstream exploded", { status: 500 })) as unknown as typeof fetch;

    await expect(evaluateApi.evaluate(BODY)).rejects.toThrow(/500/);
  });

  it("a network failure rejects instead of returning a score", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    }) as unknown as typeof fetch;

    await expect(evaluateApi.evaluate(BODY)).rejects.toThrow();
  });

  it("a timeout rejects instead of returning a score", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new DOMException("The operation was aborted.", "AbortError");
    }) as unknown as typeof fetch;

    await expect(evaluateApi.evaluate(BODY)).rejects.toThrow();
  });

  it("the rejection carries no numbers a caller could render", async () => {
    // A fallback that threw *and* attached a score would still let a careless
    // caller display one.
    globalThis.fetch = vi.fn(async () =>
      new Response("boom", { status: 503 })) as unknown as typeof fetch;

    const error = await evaluateApi.evaluate(BODY).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(Error);
    const scores = numbersIn({ ...(error as Error) }).filter((n) => n >= 1 && n <= 100);
    expect(scores).toEqual([]);
  });
});

describe("when the API succeeds but says nothing", () => {
  it("an empty object is passed through, not filled in", async () => {
    globalThis.fetch = vi.fn(async () =>
      new Response("{}", { status: 200, headers: { "content-type": "application/json" } }),
    ) as unknown as typeof fetch;

    const result = await evaluateApi.evaluate(BODY);

    // The contract is that nothing invents a score. `total_score` absent is a
    // fact the page can render as an empty state; `total_score: 79.5` arriving
    // from nowhere is the defect.
    expect(result).toEqual({});
    expect(numbersIn(result)).toEqual([]);
  });

  it("nulls stay null rather than becoming defaults", async () => {
    globalThis.fetch = vi.fn(async () =>
      new Response(JSON.stringify({ total_score: null, success_probability: null }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    ) as unknown as typeof fetch;

    const result = await evaluateApi.evaluate(BODY) as unknown as Record<string, unknown>;

    expect(result.total_score).toBeNull();
    expect(result.success_probability).toBeNull();
    expect(numbersIn(result)).toEqual([]);
  });
});

describe("the request actually goes out", () => {
  it("reaches the API rather than being answered locally", async () => {
    // Guards the whole file: if `isMock` were true, fetch would never be called
    // and every assertion above would be about a mock that cannot fail.
    const spy = vi.fn(async (input: RequestInfo | URL) => {
      void input;
      return new Response("{}", { status: 200, headers: { "content-type": "application/json" } });
    });
    globalThis.fetch = spy as unknown as typeof fetch;

    await evaluateApi.evaluate(BODY);

    expect(spy).toHaveBeenCalledTimes(1);
    expect(String(spy.mock.calls[0][0])).toContain("/api/v1/");
  });
});

describe("the exported report", () => {
  it("comes from the server, not from a template", async () => {
    // In mock mode this returns a Blob built in the browser reading
    // "SORA.earth ESG Report (mock) / Score: 72.3 / 100" over a real timestamp,
    // labelled application/pdf while being plain text. A file outlives the page
    // it came from: whoever opens it later has no way to know the number was
    // never computed.
    // A string body, not `new Blob([...])`.
    //
    // On Node 20 -- which is what CI runs -- undici's Response rejects jsdom's
    // Blob with "TypeError: object.stream is not a function", because the two
    // implementations do not share the stream interface. Node 24 accepts it, so
    // this passed locally and failed in CI. A string is understood by both, and
    // `res.blob()` in report.ts still hands back a Blob either way.
    const spy = vi.fn(async (input: RequestInfo | URL) => {
      void input;
      return new Response("%PDF-1.4 real", { status: 200 });
    });
    globalThis.fetch = spy as unknown as typeof fetch;

    const blob = await reportApi.pdf({ ...BODY });

    expect(spy).toHaveBeenCalledTimes(1);
    expect(String(spy.mock.calls[0][0])).toContain("/api/v1/report/pdf");
    expect(await blob.text()).not.toContain("(mock)");
    expect(await blob.text()).not.toContain("72.3");
  });

  it("propagates a failure instead of handing over a template", async () => {
    globalThis.fetch = vi.fn(async () =>
      new Response("nope", { status: 500 })) as unknown as typeof fetch;

    await expect(reportApi.pdf({ ...BODY })).rejects.toThrow();
  });
});
