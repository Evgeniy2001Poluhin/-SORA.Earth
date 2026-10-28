/**
 * The What-If sweep is twelve /evaluate requests in a row, and it belongs to
 * the session and the component that started it.
 *
 * It caught every error and carried on. With the real store and transport: a
 * 401 on one point ended the session, and the remaining points went out with
 * no Bearer -- A's payload, sent anonymously. After a switch to B they went out
 * with B's token. Unmounting did not stop it either.
 *
 * Point 2 of each sweep is held open; the test then acts and releases it, and
 * waits for the sweep to finish (or not) by watching the button, not a timer.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("@/api/mock", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/mock")>();
  return { ...actual, isMock: false };
});

import { WhatIf } from "./WhatIf";
import { useAuth } from "@/store/auth";
import { callsOf } from "@/test/http";
import {
  SPECIALIST_A, SPECIALIST_B, deferred, jsonResponse, renderWithQuery, routeFetch, seedSession, stubChartLayout,
} from "@/test/utils";

// The first test pays for importing recharts; under a loaded parallel run that
// can approach the 5 s default.
vi.setConfig({ testTimeout: 15_000 });

const FORM = {
  project_name: "Solar Farm", country: "Sweden", budget_usd: 150000,
  co2_reduction_tons_per_year: 340, social_impact_score: 9, project_duration_months: 18,
};
const EVALUATE = "/api/v1/evaluate";
const scored = (n: number) => jsonResponse({ total_score: 50 + n, risk_level: "Medium" });

/**
 * /evaluate answers point 1 at once, holds point 2 until released, and answers
 * the rest with `rest` (a score by default). Every request's init is kept.
 */
function serveSweep(rest: (n: number) => Response = scored) {
  const held = deferred<Response>();
  const inits: RequestInit[] = [];
  const stub = routeFetch(
    [
      ["POST", EVALUATE, ({ init }) => {
        inits.push(init);
        const n = inits.length;
        if (n === 1) return scored(n);
        if (n === 2) return held.promise;
        return rest(n);
      }],
      ["POST", "/api/v1/auth/login-json", () => jsonResponse({ access_token: "tok-b", refresh_token: "r", token_type: "bearer", expires_in: 1 })],
      ["GET", "/api/v1/auth/me", () => jsonResponse(SPECIALIST_B)],
    ],
    () => jsonResponse({}),
  );
  const sweepCalls = () => callsOf(stub).filter((c) => c.url === EVALUATE);
  return { held, inits, sweepCalls };
}

const sweepButton = () => screen.getByRole("button", { name: /Run sweep|Sweeping…/ });
const renderWhatIf = () => renderWithQuery(<WhatIf form={FORM} lastRun={{ ...FORM, total_score: 60 }} />);
const settle = () => act(async () => { for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0)); });

beforeEach(() => {
  vi.restoreAllMocks();
  stubChartLayout();
  seedSession(SPECIALIST_A, "tok-a");
});

describe("a sweep stops when its session ends", () => {
  it("stops at a 401 and sends nothing without the Bearer (the session-expired notice is the boundary's)", async () => {
    const { held, sweepCalls } = serveSweep();
    renderWhatIf();
    await userEvent.click(sweepButton());
    await waitFor(() => expect(sweepCalls()).toHaveLength(2));

    held.resolve(new Response("expired", { status: 401 }));
    await settle();

    expect(sweepCalls()).toHaveLength(2);
    expect(sweepCalls().every((c) => c.headers["Authorization"] === "Bearer tok-a")).toBe(true);
    expect(useAuth.getState().user).toBeNull();
  });

  it("stops at a sign-out", async () => {
    const { held, sweepCalls } = serveSweep();
    renderWhatIf();
    await userEvent.click(sweepButton());
    await waitFor(() => expect(sweepCalls()).toHaveLength(2));

    await act(async () => { await useAuth.getState().logout(); });
    held.resolve(scored(2));
    await settle();

    expect(sweepCalls()).toHaveLength(2);
    expect(sweepCalls().some((c) => c.headers["Authorization"] === undefined)).toBe(false);
  });

  it("sends nothing under B's token after a switch, and draws none of A's points", async () => {
    const { held, sweepCalls } = serveSweep();
    const { container } = renderWhatIf();
    await userEvent.click(sweepButton());
    await waitFor(() => expect(sweepCalls()).toHaveLength(2));

    await act(async () => { await useAuth.getState().login("spec-b", "pw"); });
    expect(useAuth.getState().user).toEqual(SPECIALIST_B);
    held.resolve(scored(2));
    await settle();

    expect(sweepCalls().some((c) => c.headers["Authorization"] === "Bearer tok-b")).toBe(false);
    expect(sweepCalls()).toHaveLength(2);
    expect(container.querySelector(".recharts-responsive-container")).toBeNull();
  });
});

describe("a sweep stops when the role is refused", () => {
  it("stops at a 403, keeps the session, and says why", async () => {
    const { held, sweepCalls } = serveSweep();
    renderWhatIf();
    await userEvent.click(sweepButton());
    await waitFor(() => expect(sweepCalls()).toHaveLength(2));

    held.resolve(new Response("no", { status: 403 }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Sweep stopped: not available for your role."));

    expect(sweepCalls()).toHaveLength(2);
    expect(sweepButton()).toHaveTextContent("Run sweep");
    expect(useAuth.getState().user).toEqual(SPECIALIST_A);
  });
});

describe("a sweep stops when its component goes", () => {
  it("aborts the request in flight and sends no more", async () => {
    const { held, inits, sweepCalls } = serveSweep();
    const { unmount } = renderWhatIf();
    await userEvent.click(sweepButton());
    await waitFor(() => expect(sweepCalls()).toHaveLength(2));

    unmount();
    expect(inits[1].signal?.aborted).toBe(true);
    held.resolve(scored(2));
    await settle();

    expect(sweepCalls()).toHaveLength(2);
  });
});

describe("a sweep that is not interrupted", () => {
  it("still sends all twelve points with the Bearer and draws them", async () => {
    // Control: the guards must not stop a sweep that nothing interrupted.
    const { held, sweepCalls } = serveSweep();
    const { container } = renderWhatIf();
    await userEvent.click(sweepButton());
    await waitFor(() => expect(sweepCalls()).toHaveLength(2));
    held.resolve(scored(2));

    await waitFor(() => expect(sweepButton()).toHaveTextContent("Run sweep"));
    expect(sweepCalls()).toHaveLength(12);
    expect(sweepCalls().every((c) => c.headers["Authorization"] === "Bearer tok-a")).toBe(true);
    expect(container.querySelector(".recharts-responsive-container")).not.toBeNull();
  });

  it("loses only the failing point on a 503, and keeps going", async () => {
    const { held, sweepCalls } = serveSweep();
    renderWhatIf();
    await userEvent.click(sweepButton());
    await waitFor(() => expect(sweepCalls()).toHaveLength(2));
    held.resolve(new Response("busy", { status: 503 }));

    await waitFor(() => expect(sweepButton()).toHaveTextContent("Run sweep"));
    expect(sweepCalls()).toHaveLength(12);
    expect(useAuth.getState().user).toEqual(SPECIALIST_A);
  });
});
