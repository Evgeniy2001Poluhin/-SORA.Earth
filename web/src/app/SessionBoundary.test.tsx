/**
 * Switching accounts in the running application.
 *
 * Rendered through the real Providers -- the application's QueryClient settings,
 * staleTime 30 s included, the session boundary and the routes -- with only
 * fetch stubbed. Each test watches every DOM change from the moment the switch
 * starts, so A's data showing for a single render fails it, not only A's data
 * on B's final screen.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("@/api/mock", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/mock")>();
  return { ...actual, isMock: false };
});
// Separate concerns with requests of their own.
vi.mock("../components/ModelBadge", () => ({ ModelBadge: () => null }));
vi.mock("./OnboardingTour", () => ({ default: () => null }));

import { App } from "./App";
import { useAuth, resetAuthForTests } from "@/store/auth";
import { callsOf } from "@/test/http";
import {
  ADMIN, SPECIALIST_A, SPECIALIST_B, deferred, jsonResponse, renderApp, routeFetch, seedSession,
} from "@/test/utils";
import type { UserInfo } from "@/api/types";

vi.setConfig({ testTimeout: 20_000 });

const LOGIN = "/api/v1/auth/login-json";
const ME = "/api/v1/auth/me";
const HISTORY = /^\/api\/v1\/history\?/;

const page = (region: string, name: string) => ({
  items: [{ id: 1, name, region, total_score: 70, success_probability: 60, risk_level: "LOW",
    budget: 100000, duration_months: 12, created_at: "2026-10-01T10:00:00Z" }],
  total: 1, limit: 20, offset: 0,
});
const bearerOf = (init: RequestInit) => (init.headers as Record<string, string>)["Authorization"];
const tokenFor = (t: string) => ({ access_token: t, refresh_token: "r", token_type: "bearer", expires_in: 1800 });

/** Records whether `needle` was ever on screen after `start()` was called. */
function watchFor(needle: string) {
  let seen = false;
  let armed = false;
  const observer = new MutationObserver(() => {
    if (armed && (document.body.textContent ?? "").includes(needle)) seen = true;
  });
  observer.observe(document.body, { subtree: true, childList: true, characterData: true });
  return {
    start() { armed = true; },
    seen: () => seen,
    stop: () => observer.disconnect(),
  };
}

let historyB: ReturnType<typeof deferred<Response>>;

function serve() {
  historyB = deferred<Response>();
  return routeFetch(
    [
      ["POST", LOGIN, ({ init }) => {
        const who = JSON.parse(String(init.body)).username;
        return jsonResponse(tokenFor(who === "spec-b" ? "tok-b" : "tok-a"));
      }],
      ["GET", ME, ({ init }) => jsonResponse(bearerOf(init) === "Bearer tok-b" ? SPECIALIST_B : SPECIALIST_A)],
      ["GET", HISTORY, ({ init }) =>
        bearerOf(init) === "Bearer tok-b" ? historyB.promise : jsonResponse(page("Alpha-Region", "Alpha project"))],
    ],
    () => jsonResponse({}),
  );
}

async function signIn(username: string, returnTo?: string) {
  await act(async () => {
    await useAuth.getState().login(username, "pw", returnTo);
  });
}

describe("A then B: nothing of A's is ever on B's screen", () => {
  let watch: ReturnType<typeof watchFor> | null = null;
  afterEach(() => watch?.stop());

  it.each([
    ["after a sign-out", true],
    ["on a direct account switch", false],
  ])("history %s", async (_label, viaLogout) => {
    serve();
    seedSession(SPECIALIST_A, "tok-a");
    const { queryClient } = renderApp("/history", <App />);
    expect(await screen.findByText("Alpha-Region")).toBeInTheDocument();

    watch = watchFor("Alpha-Region");
    watch.start();
    if (viaLogout) {
      await act(async () => { await useAuth.getState().logout(); });
    }
    await signIn("spec-b");

    // B's history is still in flight: B sees a loading table, never A's rows.
    await waitFor(() => expect(screen.getByText("My evaluations")).toBeInTheDocument());
    expect(screen.queryByText("Alpha-Region")).not.toBeInTheDocument();

    historyB.resolve(jsonResponse(page("Beta-Region", "Beta project")));
    expect(await screen.findByText("Beta-Region")).toBeInTheDocument();
    expect(watch.seen()).toBe(false);

    // Nothing keyed to A survives in the cache, though its staleTime has not run out.
    const keys = queryClient.getQueryCache().getAll().map((q) => JSON.stringify(q.queryKey));
    expect(keys.some((k) => k.includes(SPECIALIST_A.user_id))).toBe(false);
    expect(queryClient.getMutationCache().getAll()).toHaveLength(0);
  });

  it("does not keep A's form input for B", async () => {
    // B is an administrator here, who may come back to /evaluate; a specialist
    // is always sent to their history.
    routeFetch(
      [
        ["POST", LOGIN, () => jsonResponse(tokenFor("tok-b"))],
        ["GET", ME, () => jsonResponse(ADMIN)],
      ],
      () => jsonResponse({}),
    );
    seedSession(SPECIALIST_A, "tok-a");
    renderApp("/evaluate", <App />);
    const name = await screen.findByDisplayValue("Solar Farm");
    await userEvent.clear(name);
    await userEvent.type(name, "A private bid");
    expect(screen.getByDisplayValue("A private bid")).toBeInTheDocument();

    watch = watchFor("A private bid");
    watch.start();
    await signIn("root", "/evaluate");

    expect(await screen.findByDisplayValue("Solar Farm")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("A private bid")).not.toBeInTheDocument();
    expect(watch.seen()).toBe(false);
  });

  it("drops A's pending save: no mutation left, and its late answer shows nothing to B", async () => {
    const save = deferred<Response>();
    routeFetch(
      [
        ["POST", "/api/v1/evaluate?save=true", () => save.promise],
        ["POST", LOGIN, () => jsonResponse(tokenFor("tok-b"))],
        ["GET", ME, () => jsonResponse(ADMIN)],
      ],
      () => jsonResponse({}),
    );
    seedSession(SPECIALIST_A, "tok-a");
    const { queryClient } = renderApp("/evaluate", <App />);
    await userEvent.click(await screen.findByRole("button", { name: "Calculate and save" }));
    await waitFor(() => expect(queryClient.getMutationCache().getAll()).toHaveLength(1));

    await signIn("root", "/evaluate");
    expect(queryClient.getMutationCache().getAll()).toHaveLength(0);

    await act(async () => {
      save.resolve(jsonResponse({ total_score: 71, risk_level: "Low", saved: true, evaluation_id: 77 }, 201));
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(screen.queryByText(/evaluation #77/)).not.toBeInTheDocument();
    expect(await screen.findByText("No evaluation yet")).toBeInTheDocument();
  });
});

describe("before the stored session is checked", () => {
  beforeEach(() => {
    resetAuthForTests("hydrating");
  });

  it("mounts nothing and requests nothing protected until /auth/me answers", async () => {
    sessionStorage.setItem("sora.token", "tok-a");
    const me = deferred<Response>();
    const stub = routeFetch(
      [
        ["GET", ME, () => me.promise],
        ["GET", HISTORY, () => jsonResponse(page("Alpha-Region", "Alpha project"))],
      ],
      () => jsonResponse({}),
    );
    renderApp("/history", <App />);
    const hydrating = useAuth.getState().hydrate();

    expect(await screen.findByText("Checking your session…")).toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 50));
    expect(callsOf(stub).map((c) => c.url)).toEqual([ME]);

    await act(async () => {
      me.resolve(jsonResponse(SPECIALIST_A as UserInfo));
      await hydrating;
    });
    expect(await screen.findByText("Alpha-Region")).toBeInTheDocument();
    const historyCall = callsOf(stub).find((c) => HISTORY.test(c.url));
    expect(historyCall?.headers["Authorization"]).toBe("Bearer tok-a");
  });
});
