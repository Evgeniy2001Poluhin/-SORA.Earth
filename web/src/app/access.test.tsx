/**
 * The role rule shared by the menu and the route guard (app/access.ts), and
 * where a sign-in lands.
 *
 * Hiding a menu entry is not a guard: /copilot typed into the address bar used
 * to mount the Co-Pilot page for a specialist, and its stream went out without
 * a Bearer, so the server could not refuse it either. And every sign-in went to
 * /mlops, whatever the role.
 */
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("@/api/mock", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/mock")>();
  return { ...actual, isMock: false };
});
vi.mock("../components/ModelBadge", () => ({ ModelBadge: () => null }));
vi.mock("./OnboardingTour", () => ({ default: () => null }));

import { App } from "./App";
import { canOpen, landingPath, navFor, safeReturnTo, saveAccess } from "./access";
import { callsOf } from "@/test/http";
import { ADMIN, ANALYST, SPECIALIST_A, jsonResponse, renderApp, routeFetch, seedSession } from "@/test/utils";

vi.setConfig({ testTimeout: 15_000 });

describe("the rule", () => {
  it("refuses Co-Pilot to a specialist only", () => {
    expect(canOpen("/copilot", SPECIALIST_A)).toBe(false);
    expect(canOpen("/copilot?x=1", SPECIALIST_A)).toBe(false);
    expect(canOpen("/copilot", ANALYST)).toBe(true);
    expect(canOpen("/copilot", ADMIN)).toBe(true);
    expect(canOpen("/copilot", null)).toBe(true);
    expect(canOpen("/history", SPECIALIST_A)).toBe(true);
    expect(canOpen("/evaluate", SPECIALIST_A)).toBe(true);
  });

  it("builds the menu from the same rule", () => {
    const menu = navFor(SPECIALIST_A).map((n) => n.to);
    expect(menu).not.toContain("/copilot");
    expect(menu).toEqual(expect.arrayContaining(["/evaluate", "/history"]));
  });

  it("lets specialist and admin save, and nobody else", () => {
    expect(saveAccess(SPECIALIST_A)).toBe("allowed");
    expect(saveAccess(ADMIN)).toBe("allowed");
    expect(saveAccess(ANALYST)).toBe("forbidden");
    expect(saveAccess(null)).toBe("anonymous");
  });

  it("sends a specialist to their history, and others to a safe return path or their start", () => {
    expect(landingPath(SPECIALIST_A, "/evaluate")).toBe("/history");
    expect(landingPath(ADMIN, null)).toBe("/mlops");
    expect(landingPath(ANALYST, null)).toBe("/");
    expect(landingPath(ANALYST, "/drift?x=1")).toBe("/drift?x=1");
    expect(landingPath(ANALYST, "//evil.example/")).toBe("/");
    expect(landingPath(ANALYST, "https://evil.example/")).toBe("/");
    expect(landingPath(ANALYST, "/login")).toBe("/");
    expect(safeReturnTo("/\\evil.example")).toBeNull();
  });
});

describe("the route guard", () => {
  it("refuses /copilot to a specialist before the page mounts or sends anything", async () => {
    const stub = routeFetch([], () => jsonResponse({}));
    seedSession(SPECIALIST_A, "tok-a");
    renderApp("/copilot", <App />);

    expect(await screen.findByText("Not available for your role")).toBeInTheDocument();
    expect(screen.queryByText(/Why this/)).not.toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 50));
    expect(callsOf(stub).some((c) => c.url.includes("/copilot") || c.url.includes("/predict/explain"))).toBe(false);
  });

  it("still opens /copilot for an analyst", async () => {
    routeFetch([], () => jsonResponse({ sessions: [] }));
    seedSession(ANALYST, "tok-ana");
    renderApp("/copilot", <App />);
    expect(await screen.findByText(/Why this/)).toBeInTheDocument();
  });
});

describe("signing in", () => {
  it("takes a specialist to their history, not to MLOps", async () => {
    routeFetch(
      [
        ["POST", "/api/v1/auth/login-json", () => jsonResponse({ access_token: "tok-a", refresh_token: "r", token_type: "bearer", expires_in: 1 })],
        ["GET", "/api/v1/auth/me", () => jsonResponse(SPECIALIST_A)],
        ["GET", /^\/api\/v1\/history\?/, () => jsonResponse({ items: [], total: 0, limit: 20, offset: 0 })],
      ],
      () => jsonResponse({}),
    );
    renderApp("/login?next=%2Fmlops", <App />);

    const user = userEvent.setup();
    // No account is offered in the form.
    expect(screen.getByLabelText("Username")).toHaveValue("");
    expect(screen.getByLabelText("Password")).toHaveValue("");
    await user.type(screen.getByLabelText("Username"), "spec-a");
    await user.type(screen.getByLabelText("Password"), "pw");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(screen.getByRole("heading", { name: "My evaluations" })).toBeInTheDocument());
    expect(screen.queryByText(/Retrain/i)).not.toBeInTheDocument();
  });
});
