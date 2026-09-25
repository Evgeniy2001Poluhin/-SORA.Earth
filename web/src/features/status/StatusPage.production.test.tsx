/**
 * The public status page against an unhealthy backend (#236).
 *
 * `status_summary()` has no `except`, so a database error propagates to a 500.
 * The page fetched with `.then(r => r.json())` -- no `r.ok` check -- so the
 * 500's error body (`{detail: ...}`) became the state, passed the `!d` guard,
 * and crashed on `d.components.map`. A status page that white-screens exactly
 * when the backend is down is the failure this guards against.
 *
 * A guarded component and a crashed one both leave an empty container, so the
 * DOM cannot tell them apart -- the thrown TypeError is the only observable
 * difference, so that is what is asserted (as in MapPage.production.test).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import StatusPage from "./StatusPage";
import { renderWithQuery } from "@/test/utils";
import { stubJson, stubStatus } from "@/test/http";

function captureTypeErrors(): string[] {
  const thrown: string[] = [];
  vi.spyOn(console, "error").mockImplementation((...args) => {
    thrown.push(args.map(String).join(" "));
  });
  return thrown;
}

describe("StatusPage survives an unhealthy backend", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("does not crash when /status/uptime answers 500 (its error body is not a status)", async () => {
    stubStatus(500, JSON.stringify({ detail: "database is down" }));
    const thrown = captureTypeErrors();

    renderWithQuery(<StatusPage />);
    await new Promise((resolve) => setTimeout(resolve, 200));

    expect(thrown.filter((m) => /TypeError/.test(m))).toEqual([]);
  });

  it("does not crash on a 200 that carries no components", async () => {
    stubJson({});
    const thrown = captureTypeErrors();

    renderWithQuery(<StatusPage />);
    await new Promise((resolve) => setTimeout(resolve, 200));

    expect(thrown.filter((m) => /TypeError/.test(m))).toEqual([]);
  });

  it("renders the components on a well-formed 200", async () => {
    stubJson({
      overall: "operational",
      components: [
        { component: "api", ok: true },
        { component: "database", ok: false },
      ],
    });

    const { findByText } = renderWithQuery(<StatusPage />);

    expect(await findByText("API")).toBeTruthy();
    expect(await findByText("Database")).toBeTruthy();
  });

  it("shows no uptime columns when response has only component and ok", async () => {
    stubJson({
      overall: "operational",
      components: [
        { component: "api", ok: true },
        { component: "database", ok: true },
      ],
    });

    const { container, findByText } = renderWithQuery(<StatusPage />);

    // Wait for the page to render
    expect(await findByText("API")).toBeTruthy();
    expect(await findByText("Database")).toBeTruthy();

    // The word "uptime" should not appear anywhere in the rendered page
    const text = container.textContent || "";
    expect(text.toLowerCase()).not.toMatch(/uptime/);
  });
});
