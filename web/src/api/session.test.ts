/**
 * The transport's 401 policy and its session-epoch check.
 *
 * - A 401 to the current session's Bearer ends that session -- once, with no
 *   refresh call and no anonymous replay of the request.
 * - 403 and 503 are not a sign-out.
 * - A 401 from a request A started does not end B's session.
 * - A JSON body or a Blob that finishes reading after the session changed is
 *   refused, even though fetch itself resolved in time.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, apiBlob, auth } from "./client";
import { session, SessionChangedError } from "@/lib/session";
import { resetAuthForTests, useAuth } from "@/store/auth";
import { callsOf } from "@/test/http";
import { SPECIALIST_A, SPECIALIST_B, deferred, jsonResponse, routeFetch, seedSession } from "@/test/utils";

const settle = () => new Promise((r) => setTimeout(r, 0));

beforeEach(() => {
  resetAuthForTests("ready");
  seedSession(SPECIALIST_A, "tok-a");
});

describe("a 401 to the session's own token", () => {
  it("ends the session, says it expired, and sends nothing else", async () => {
    const stub = routeFetch([["GET", "/api/v1/history", () => new Response("expired", { status: 401 })]]);

    await expect(api("/history")).rejects.toMatchObject({ status: 401 });
    await settle();

    expect(useAuth.getState()).toMatchObject({ phase: "ready", user: null, token: null, notice: "expired" });
    expect(auth.get()).toBeNull();
    expect(sessionStorage.getItem("sora.token")).toBeNull();
    // No refresh, and no replay without the Bearer.
    expect(stub).toHaveBeenCalledTimes(1);
  });

  it.each([403, 503])("does not sign the user out on %i", async (status) => {
    routeFetch([["GET", "/api/v1/history", () => new Response("no", { status })]]);

    await expect(api("/history")).rejects.toBeInstanceOf(ApiError);
    await settle();

    expect(useAuth.getState().user).toEqual(SPECIALIST_A);
    expect(auth.get()).toBe("tok-a");
  });

  it("does not end the session for a transport failure", async () => {
    globalThis.fetch = vi.fn(async () => { throw new TypeError("Failed to fetch"); }) as unknown as typeof fetch;

    await expect(api("/history")).rejects.toBeInstanceOf(TypeError);
    expect(useAuth.getState().user).toEqual(SPECIALIST_A);
  });
});

describe("a response from a session that has ended", () => {
  it("does not let A's late 401 sign B out", async () => {
    const late = deferred<Response>();
    routeFetch([["GET", "/api/v1/history", () => late.promise]]);
    const fromA = api("/history");
    await settle();

    // A signs out and B signs in while A's request is still out.
    routeFetch([
      ["POST", "/api/v1/auth/login-json", () => jsonResponse({ access_token: "tok-b", refresh_token: "r", token_type: "bearer", expires_in: 1 })],
      ["GET", "/api/v1/auth/me", () => jsonResponse(SPECIALIST_B)],
    ]);
    await useAuth.getState().login("spec-b", "pw");

    late.resolve(new Response("expired", { status: 401 }));
    await expect(fromA).rejects.toBeInstanceOf(SessionChangedError);
    await settle();

    expect(useAuth.getState().user).toEqual(SPECIALIST_B);
    expect(auth.get()).toBe("tok-b");
  });

  it("refuses JSON that finishes reading after the session changed", async () => {
    const body = deferred<unknown>();
    globalThis.fetch = vi.fn(async () => ({ ok: true, status: 200, json: () => body.promise }) as unknown as Response) as unknown as typeof fetch;

    const reading = api<{ owner: string }>("/history/7");
    await settle();
    await useAuth.getState().logout();
    body.resolve({ owner: "A" });

    await expect(reading).rejects.toBeInstanceOf(SessionChangedError);
  });

  it("refuses a Blob that finishes reading after the session changed", async () => {
    const blob = deferred<Blob>();
    globalThis.fetch = vi.fn(async () => ({ ok: true, status: 200, blob: () => blob.promise }) as unknown as Response) as unknown as typeof fetch;

    const reading = apiBlob("/export/csv");
    await settle();
    await useAuth.getState().logout();
    blob.resolve(new Blob(["A's rows"]));

    await expect(reading).rejects.toBeInstanceOf(SessionChangedError);
  });

  it("aborts the request itself when the session ends", async () => {
    let seen: AbortSignal | undefined;
    globalThis.fetch = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      seen = init?.signal ?? undefined;
      return new Promise<Response>(() => {});
    }) as unknown as typeof fetch;

    void api("/history").catch(() => {});
    await settle();
    expect(seen?.aborted).toBe(false);
    await useAuth.getState().logout();
    expect(seen?.aborted).toBe(true);
  });
});

describe("what a signed-in request carries", () => {
  it("sends the Bearer and keeps the answer out of the HTTP cache", async () => {
    const stub = routeFetch([["GET", "/api/v1/history", () => jsonResponse({ items: [], total: 0, limit: 20, offset: 0 })]]);
    await api("/history");
    expect(callsOf(stub)[0].headers["Authorization"]).toBe("Bearer tok-a");
    expect((stub.mock.calls[0][1] as RequestInit).cache).toBe("no-store");
    expect(session.isCurrent(useAuth.getState().epoch)).toBe(true);
  });
});
