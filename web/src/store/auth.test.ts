/**
 * The session store: one transition, and nothing published before it is known.
 *
 * Each case names the defect it exists to catch. The store, the transport and
 * the session epoch are the real ones; only fetch is stubbed, and responses are
 * held open with deferred promises so the in-between states can be read.
 */
import { beforeEach, describe, expect, it } from "vitest";
import { QueryClient } from "@tanstack/react-query";
import { auth } from "@/api/client";
import { session } from "@/lib/session";
import { resetAuthForTests, useAuth } from "./auth";
import { callsOf } from "@/test/http";
import { SPECIALIST_A, SPECIALIST_B, deferred, jsonResponse, routeFetch, seedSession } from "@/test/utils";

const LOGIN = "/api/v1/auth/login-json";
const ME = "/api/v1/auth/me";
const token = (t: string) => ({ access_token: t, refresh_token: "r-" + t, token_type: "bearer", expires_in: 1800 });
const bearerOf = (init: RequestInit) => (init.headers as Record<string, string>)["Authorization"];
const stored = () => sessionStorage.getItem("sora.token");

describe("hydrate: the stored token is checked before it is used", () => {
  beforeEach(() => resetAuthForTests("hydrating"));

  it("removes a token /auth/me refuses, from the client and from storage", async () => {
    sessionStorage.setItem("sora.token", "tok-dead");
    routeFetch([["GET", ME, () => new Response("expired", { status: 401 })]]);

    await useAuth.getState().hydrate();

    expect(useAuth.getState()).toMatchObject({ phase: "ready", user: null, token: null });
    expect(auth.get()).toBeNull();
    expect(stored()).toBeNull();
  });

  it("does not sign in, or continue as anonymous, when /auth/me is unavailable", async () => {
    sessionStorage.setItem("sora.token", "tok-a");
    routeFetch([["GET", ME, () => new Response("down", { status: 503 })]]);

    await useAuth.getState().hydrate();

    expect(useAuth.getState().phase).toBe("unverified");
    expect(useAuth.getState().user).toBeNull();
    expect(auth.get()).toBeNull();
  });

  it("publishes the account only after /auth/me answers for it", async () => {
    sessionStorage.setItem("sora.token", "tok-a");
    const me = deferred<Response>();
    routeFetch([["GET", ME, () => me.promise]]);

    const done = useAuth.getState().hydrate();
    await Promise.resolve();
    expect(useAuth.getState().phase).toBe("hydrating");
    expect(auth.get()).toBeNull();

    me.resolve(jsonResponse(SPECIALIST_A));
    await done;
    expect(useAuth.getState()).toMatchObject({ phase: "ready", user: SPECIALIST_A, token: "tok-a" });
    expect(auth.get()).toBe("tok-a");
  });

  it("does not restore A when A's late /auth/me answers after B signed in", async () => {
    sessionStorage.setItem("sora.token", "tok-a");
    const meA = deferred<Response>();
    routeFetch([
      ["POST", LOGIN, () => jsonResponse(token("tok-b"))],
      ["GET", ME, ({ init }) => (bearerOf(init) === "Bearer tok-a" ? meA.promise : jsonResponse(SPECIALIST_B))],
    ]);

    const hydrating = useAuth.getState().hydrate();
    await useAuth.getState().login("spec-b", "pw");
    meA.resolve(jsonResponse(SPECIALIST_A));
    await hydrating;

    expect(useAuth.getState().user).toEqual(SPECIALIST_B);
    expect(auth.get()).toBe("tok-b");
    expect(stored()).toBe("tok-b");
  });
});

describe("login: a candidate token is not the application's until /auth/me says whose it is", () => {
  beforeEach(() => resetAuthForTests("ready"));

  it("does not publish B, or hand its token to the transport, while /auth/me is pending", async () => {
    const me = deferred<Response>();
    routeFetch([
      ["POST", LOGIN, () => jsonResponse(token("tok-b"))],
      ["GET", ME, () => me.promise],
    ]);

    const login = useAuth.getState().login("spec-b", "pw");
    await new Promise((r) => setTimeout(r, 0));
    expect(useAuth.getState().phase).toBe("transitioning");
    expect(useAuth.getState().user).toBeNull();
    expect(useAuth.getState().token).toBeNull();
    expect(auth.get()).toBeNull();
    expect(stored()).toBeNull();

    me.resolve(jsonResponse(SPECIALIST_B));
    await login;
    expect(useAuth.getState()).toMatchObject({ phase: "ready", user: SPECIALIST_B, token: "tok-b" });
  });

  it("leaves no token anywhere when /auth/me fails after a successful login", async () => {
    routeFetch([
      ["POST", LOGIN, () => jsonResponse(token("tok-b"))],
      ["GET", ME, () => new Response("down", { status: 503 })],
    ]);

    await expect(useAuth.getState().login("spec-b", "pw")).rejects.toThrow();

    expect(useAuth.getState()).toMatchObject({ phase: "ready", user: null, token: null });
    expect(auth.get()).toBeNull();
    expect(stored()).toBeNull();
  });

  it("checks the candidate with its own token, and signs in without A's", async () => {
    seedSession(SPECIALIST_A, "tok-a");
    const stub = routeFetch([
      ["POST", LOGIN, () => jsonResponse(token("tok-b"))],
      ["GET", ME, () => jsonResponse(SPECIALIST_B)],
    ]);

    await useAuth.getState().login("spec-b", "pw");

    const [loginCall, meCall] = callsOf(stub);
    expect(loginCall.headers["Authorization"]).toBeUndefined();
    expect(meCall.headers["Authorization"]).toBe("Bearer tok-b");
  });

  it("does not publish A when A's login finishes after the user signed out", async () => {
    const me = deferred<Response>();
    routeFetch([
      ["POST", LOGIN, () => jsonResponse(token("tok-a"))],
      ["GET", ME, () => me.promise],
    ]);

    const login = useAuth.getState().login("spec-a", "pw");
    await new Promise((r) => setTimeout(r, 0));
    await useAuth.getState().logout();
    me.resolve(jsonResponse(SPECIALIST_A));
    await expect(login).rejects.toThrow();

    expect(useAuth.getState()).toMatchObject({ phase: "ready", user: null, token: null });
    expect(auth.get()).toBeNull();
    expect(stored()).toBeNull();
  });

  it("sends a specialist to their own history, not to MLOps", async () => {
    routeFetch([
      ["POST", LOGIN, () => jsonResponse(token("tok-a"))],
      ["GET", ME, () => jsonResponse(SPECIALIST_A)],
    ]);
    await useAuth.getState().login("spec-a", "pw", "/mlops");
    expect(useAuth.getState().redirect).toBe("/history");
  });
});

describe("the transition empties what the previous session held", () => {
  beforeEach(() => resetAuthForTests("ready"));

  it("aborts the old session's requests and clears both caches", async () => {
    const qc = new QueryClient();
    session.bindQueryClient(qc);
    seedSession(SPECIALIST_A, "tok-a");
    qc.setQueryData(["history", SPECIALIST_A.user_id, 0, {}], { items: [{ id: 1 }] });
    const signal = session.signal();

    await useAuth.getState().logout();

    expect(signal.aborted).toBe(true);
    expect(qc.getQueryCache().getAll()).toHaveLength(0);
    expect(qc.getMutationCache().getAll()).toHaveLength(0);
    expect(auth.get()).toBeNull();
    expect(stored()).toBeNull();
    expect(useAuth.getState().epoch).toBe(session.epoch());
  });
});
