/**
 * The API client's error type (#250).
 *
 * The frontend had exactly one place that told two 4xx cases apart, and it did
 * it by asking whether the error message contained the English sentence
 * "fit baseline first". Rewording that sentence, adding a full stop or
 * translating it would have turned a helpful hint into a generic failure, with
 * nothing anywhere going red.
 *
 * Measured before writing this: across the non-test frontend there are nine
 * `.includes` / `.startsWith` / `.match` / `.indexOf` calls, and exactly one
 * reads a server error's text. The other error-shaped one,
 * `SchedulerPanel.tsx:36`, matches a string the same file produced two lines
 * earlier -- a local convention, not a contract across a boundary.
 *
 * The fix has to keep `message` byte-identical, because every other consumer
 * reads it and nothing else about them changes.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "./client";

/** A failing response with a body, built as a real `Response` so `text()` works. */
function stubFailure(status: number, body: string) {
  const stub = vi.fn(async () => new Response(body, { status, statusText: "Bad Request" }));
  globalThis.fetch = stub as unknown as typeof fetch;
  return stub;
}

describe("ApiError", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("keeps the message every existing consumer already reads", async () => {
    // Not a stylistic preference. `errorMessage(e)` feeds toasts, the error
    // boundary and several panels; widening the thrown type is only safe while
    // what they read is unchanged. The string is asserted in full rather than
    // by a pattern, because "close enough" is what would break them.
    stubFailure(400, "fit baseline first");

    await expect(api("/mlops/drift/simulate", { method: "POST" })).rejects.toThrow(
      "API 400: fit baseline first",
    );
  });

  it("falls back to statusText when the body is empty, as it always did", async () => {
    stubFailure(400, "");

    await expect(api("/x")).rejects.toThrow("API 400: Bad Request");
  });

  it("carries the status and the parsed body", async () => {
    stubFailure(400, JSON.stringify({ status: "not_fitted", reason_code: "baseline_not_fitted" }));

    const error = await api("/x").catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(400);
    expect((error as ApiError).body).toEqual({
      status: "not_fitted",
      reason_code: "baseline_not_fitted",
    });
  });

  it("reads reason_code from a handler that returned a model at the top level", async () => {
    stubFailure(400, JSON.stringify({ status: "not_fitted", reason_code: "baseline_not_fitted" }));

    const error = (await api("/x").catch((e: unknown) => e)) as ApiError;

    expect(error.reasonCode).toBe("baseline_not_fitted");
  });

  it("reads reason_code from a handler that raised HTTPException with a dict detail", async () => {
    // FastAPI nests the payload under `detail` for that form. Both shapes are
    // in this codebase, so both are read -- a client that understood only one
    // would silently take the generic branch for the other.
    stubFailure(409, JSON.stringify({ detail: { reason_code: "already_running", message: "no" } }));

    const error = (await api("/x").catch((e: unknown) => e)) as ApiError;

    expect(error.reasonCode).toBe("already_running");
  });

  it("gives null rather than a guess when the body carries no reason_code", async () => {
    // A wrong branch selector is worse than none: it looks like it worked.
    stubFailure(400, JSON.stringify({ detail: "fit baseline first" }));

    const error = (await api("/x").catch((e: unknown) => e)) as ApiError;

    expect(error.reasonCode).toBeNull();
  });

  it("survives a body that is not JSON at all", async () => {
    // nginx answers 502 with HTML. Parsing must not throw over the top of the
    // real error, which is what the caller needs to see.
    stubFailure(502, "<html><body>502 Bad Gateway</body></html>");

    const error = (await api("/x").catch((e: unknown) => e)) as ApiError;

    expect(error.status).toBe(502);
    expect(error.reasonCode).toBeNull();
    expect(error.body).toBe("<html><body>502 Bad Gateway</body></html>");
    expect(error.message).toContain("502 Bad Gateway");
  });

  it("ignores a non-string reason_code instead of coercing it", async () => {
    stubFailure(400, JSON.stringify({ reason_code: 42 }));

    const error = (await api("/x").catch((e: unknown) => e)) as ApiError;

    expect(error.reasonCode).toBeNull();
  });

  it("is still an Error, so every catch and boundary keeps working", async () => {
    stubFailure(400, "no");

    const error = await api("/x").catch((e: unknown) => e);

    expect(error).toBeInstanceOf(Error);
    expect((error as Error).name).toBe("ApiError");
  });

  it("does not wrap a successful response", async () => {
    globalThis.fetch = vi.fn(async () =>
      new Response(JSON.stringify({ ok: 1 }), { status: 200 }),
    ) as unknown as typeof fetch;

    await expect(api("/x")).resolves.toEqual({ ok: 1 });
  });
});
