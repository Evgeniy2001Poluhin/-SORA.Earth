/**
 * A saved evaluation opened from History (/evaluate?snapshot=ID).
 *
 * It used to be one fetch with `.catch(() => {})`: a 401, a 404 or a 503 left
 * the Solar Farm preset in the form, looking like the saved record. A null
 * column reached `.toLocaleString` and the stored region was put into the
 * country field. Now each failure says what it is, the record is shown as
 * stored without recalculating, and a late answer for a previous account never
 * reaches the next one.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

vi.mock("@/api/mock", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/mock")>();
  return { ...actual, isMock: false };
});
vi.mock("@/components/ModelBadge", () => ({ ModelBadge: () => null }));
vi.mock("@/app/OnboardingTour", () => ({ default: () => null }));

import { EvaluatePage } from "./EvaluatePage";
import { App } from "@/app/App";
import { useAuth } from "@/store/auth";
import { callsOf } from "@/test/http";
import {
  ADMIN, SPECIALIST_A, deferred, jsonResponse, renderApp, renderWithQuery, routeFetch, seedSession,
} from "@/test/utils";

vi.setConfig({ testTimeout: 15_000 });

const ROW = {
  id: 7, name: "Peat restoration", budget: 250000, co2_reduction: 410, social_impact: 6,
  duration_months: 30, total_score: 64.2, environment_score: 70.1, social_score: 58.3,
  economic_score: 61.9, success_probability: 57.5, recommendation: null, risk_level: "MED",
  region: "Europe", lat: 50, lon: 10, created_at: "2026-10-20T08:00:00Z",
};
const NULL_ROW = Object.fromEntries(Object.keys(ROW).map((k) => [k, k === "id" ? 7 : null]));

const renderAt = (path: string) =>
  renderWithQuery(<MemoryRouter initialEntries={[path]}><EvaluatePage /></MemoryRouter>);
const card = () => screen.getByRole("region", { name: "Saved evaluation" });

beforeEach(() => {
  vi.restoreAllMocks();
  seedSession(SPECIALIST_A, "tok-a");
});

describe("a failed load is said as a failure", () => {
  it.each([
    [401, "Sign in to open this evaluation."],
    [403, "Saved evaluations are not available for your role."],
    [404, "Evaluation not found or not available."],
    [503, "Could not load the evaluation"],
  ])("%i", async (status, message) => {
    routeFetch([["GET", "/api/v1/history/7", () => new Response("no", { status })]], () => jsonResponse({}));
    renderAt("/evaluate?snapshot=7");

    await waitFor(() => expect(within(card()).getByRole("alert")).toHaveTextContent(message));
    // Nothing from a preset stands in for the record.
    expect(card().textContent).not.toContain("Solar Farm");
    expect(card().textContent).not.toMatch(/\d+\.\d/);
    expect(within(card()).queryByRole("button", { name: /Use these inputs/ })).not.toBeInTheDocument();
  });

  it("an id that is not an id is not found, and nothing is requested", async () => {
    const stub = routeFetch([], () => jsonResponse({}));
    renderAt("/evaluate?snapshot=abc");
    expect(within(card()).getByRole("alert")).toHaveTextContent("Evaluation not found or not available.");
    expect(callsOf(stub).some((c) => c.url.includes("/history/"))).toBe(false);
  });
});

describe("the record as stored", () => {
  it("shows the stored values with the Bearer and no-store, and does not recalculate", async () => {
    const stub = routeFetch([["GET", "/api/v1/history/7", () => jsonResponse(ROW)]], () => jsonResponse({}));
    renderAt("/evaluate?snapshot=7");

    expect(await within(card()).findByText("Peat restoration")).toBeInTheDocument();
    expect(card().textContent).toContain("64.2");
    expect(card().textContent).toContain("Europe");
    const get = callsOf(stub).find((c) => c.url === "/api/v1/history/7")!;
    expect(get.headers["Authorization"]).toBe("Bearer tok-a");
    expect((stub.mock.calls.find(([u]) => String(u) === "/api/v1/history/7")![1] as RequestInit).cache).toBe("no-store");
    await new Promise((r) => setTimeout(r, 30));
    expect(callsOf(stub).some((c) => c.method === "POST")).toBe(false);
  });

  it("renders a row whose every column is null, and will not reuse its inputs", async () => {
    routeFetch([["GET", "/api/v1/history/7", () => jsonResponse(NULL_ROW)]], () => jsonResponse({}));
    renderAt("/evaluate?snapshot=7");

    expect(await within(card()).findByText("Unnamed evaluation")).toBeInTheDocument();
    expect(within(card()).getByRole("button", { name: "Inputs incomplete: cannot reuse" })).toBeDisabled();
  });

  it("reuses the inputs, but not the region as the country", async () => {
    routeFetch([["GET", "/api/v1/history/7", () => jsonResponse(ROW)]], () => jsonResponse({}));
    renderAt("/evaluate?snapshot=7");
    await userEvent.click(await within(card()).findByRole("button", { name: "Use these inputs in the form" }));

    expect(screen.getByDisplayValue("Peat restoration")).toBeInTheDocument();
    expect(screen.getByDisplayValue("250000")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("Europe")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Calculate" })).toBeDisabled();
    expect(screen.getByText("Select a country to calculate.")).toBeInTheDocument();
  });
});

describe("a late answer for a previous account", () => {
  let observer: MutationObserver | null = null;
  afterEach(() => observer?.disconnect());

  it("does not reach the next account's screen", async () => {
    const forA = deferred<Response>();
    routeFetch(
      [
        ["GET", "/api/v1/history/7", ({ init }) =>
          (init.headers as Record<string, string>)["Authorization"] === "Bearer tok-a"
            ? forA.promise
            : jsonResponse({ ...ROW, name: "B's own record" })],
        ["POST", "/api/v1/auth/login-json", () => jsonResponse({ access_token: "tok-b", refresh_token: "r", token_type: "bearer", expires_in: 1 })],
        ["GET", "/api/v1/auth/me", () => jsonResponse(ADMIN)],
      ],
      () => jsonResponse({}),
    );
    renderApp("/evaluate?snapshot=7", <App />);
    expect(await screen.findByText("Loading the saved evaluation…")).toBeInTheDocument();

    let leaked = false;
    observer = new MutationObserver(() => {
      if ((document.body.textContent ?? "").includes("A's secret record")) leaked = true;
    });
    observer.observe(document.body, { subtree: true, childList: true, characterData: true });

    await act(async () => {
      await useAuth.getState().login("root", "pw", "/evaluate?snapshot=7");
    });
    expect(await screen.findByText("B's own record")).toBeInTheDocument();

    await act(async () => {
      forA.resolve(jsonResponse({ ...ROW, name: "A's secret record" }));
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(screen.getByText("B's own record")).toBeInTheDocument();
    expect(leaked).toBe(false);
  });
});
