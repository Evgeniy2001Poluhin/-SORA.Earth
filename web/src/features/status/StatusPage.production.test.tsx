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
import { act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import StatusPage from "./StatusPage";
import { renderWithQuery } from "@/test/utils";
import { stubJson, stubStatus } from "@/test/http";

const ALL_UP = JSON.stringify({
  overall: "operational",
  components: [
    { component: "api", ok: true },
    { component: "models", ok: true },
    { component: "database", ok: true },
    { component: "external_data", ok: true },
  ],
});

/** Answers each fetch in turn; the last answer repeats for any later call. */
function stubAnswers(...answers: Array<{ status: number; body: string }>) {
  let call = 0;
  const stub = vi.fn(async () => {
    const answer = answers[Math.min(call, answers.length - 1)];
    call += 1;
    return new Response(answer.body, {
      status: answer.status,
      headers: { "content-type": "application/json" },
    });
  });
  globalThis.fetch = stub as unknown as typeof fetch;
  return stub;
}

/** Lets a resolved fetch reach the screen; TanStack notifies on a timer. */
async function settle() {
  await act(() => new Promise<void>((resolve) => setTimeout(resolve, 50)));
}

/** Fires the page's own 30-second refresh timer, then lets it land. */
async function tickRefresh() {
  await act(async () => {
    vi.advanceTimersByTime(30_000);
  });
  await settle();
}

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

describe("StatusPage does not keep saying operational after a failed refresh (F053)", () => {
  beforeEach(() => {
    // Only the interval is faked: it is the page's own 30-second refresh timer.
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("drops the green headline when the 30-second refresh fails", async () => {
    const fetchMock = stubAnswers(
      { status: 200, body: ALL_UP },
      { status: 500, body: JSON.stringify({ detail: "database is down" }) },
    );
    const { getByText, queryByText, container } = renderWithQuery(<StatusPage />);
    await settle();
    expect(getByText("All systems operational")).toBeTruthy();

    await tickRefresh();

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(queryByText("All systems operational")).toBeNull();
    expect(getByText("Current status unknown")).toBeTruthy();
    expect(container.textContent).toMatch(/Last successful check:/);
    // The last successful rows stay on the page, labelled, rather than vanishing.
    expect(getByText("API")).toBeTruthy();
  });

  it("returns to operational once a later refresh succeeds", async () => {
    stubAnswers(
      { status: 200, body: ALL_UP },
      { status: 500, body: "database is down" },
      { status: 200, body: ALL_UP },
    );
    const { getByText, queryByText } = renderWithQuery(<StatusPage />);
    await settle();

    await tickRefresh();
    expect(getByText("Current status unknown")).toBeTruthy();

    await tickRefresh();
    expect(getByText("All systems operational")).toBeTruthy();
    expect(queryByText("Current status unknown")).toBeNull();
  });

  it("stays neutral when the very first answer is a failure", async () => {
    stubAnswers({ status: 500, body: "database is down" });
    const { getByText, queryByText } = renderWithQuery(<StatusPage />);
    await settle();

    expect(getByText("Status temporarily unavailable")).toBeTruthy();
    expect(queryByText("All systems operational")).toBeNull();
  });
});

describe("StatusPage reads components[].ok as a boolean only (F054)", () => {
  it("shows a component with no ok field as Unknown, not as Down", async () => {
    stubJson({
      overall: "operational",
      components: [
        { component: "api", ok: true },
        { component: "database" },
      ],
    });

    const { findByText, getByText, queryByText } = renderWithQuery(<StatusPage />);

    expect(await findByText("Database")).toBeTruthy();
    expect(getByText("● Unknown")).toBeTruthy();
    expect(queryByText("● Down")).toBeNull();
    expect(queryByText("All systems operational")).toBeNull();
    expect(getByText("Status incomplete")).toBeTruthy();
  });

  it("treats a non-boolean ok (0 or the string false) as Unknown, not as a measured Down", async () => {
    stubJson({
      overall: "operational",
      components: [
        { component: "api", ok: true },
        { component: "models", ok: 0 },
        { component: "database", ok: "false" },
        { component: "external_data", ok: true },
      ],
    });

    const { findByText, getAllByText, queryByText } = renderWithQuery(<StatusPage />);

    expect(await findByText("ML Models")).toBeTruthy();
    expect(getAllByText("● Unknown")).toHaveLength(2);
    expect(queryByText("● Down")).toBeNull();
  });

  it("still renders a real false as Down, and the page as degraded", async () => {
    stubJson({
      overall: "degraded",
      components: [
        { component: "api", ok: true },
        { component: "database", ok: false },
      ],
    });

    const { findByText, getByText, queryByText } = renderWithQuery(<StatusPage />);

    expect(await findByText("● Down")).toBeTruthy();
    expect(getByText("Degraded performance")).toBeTruthy();
    expect(queryByText("● Unknown")).toBeNull();
  });

  it("still renders real true values as Operational under the all-clear headline", async () => {
    stubJson(JSON.parse(ALL_UP));

    const { findAllByText, getByText, queryByText } = renderWithQuery(<StatusPage />);

    expect(await findAllByText("● Operational")).toHaveLength(4);
    expect(getByText("All systems operational")).toBeTruthy();
    expect(queryByText("● Unknown")).toBeNull();
  });
});
