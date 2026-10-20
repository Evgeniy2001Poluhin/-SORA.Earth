/**
 * The header pill reports the service's health (audit F001).
 *
 * It said "Operational" as a literal on every page, whatever the service was
 * doing. It now reads GET /api/v1/health through the query the Evaluate hero
 * uses, and says Operational only for a healthy answer. A failed or empty answer
 * reads Unknown, and so does a refetch that fails after an earlier success:
 * TanStack keeps the old data, so the pill must look at the error, not the data.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { QueryClient } from "@tanstack/react-query";

vi.mock("@/api/mock", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/mock")>();
  return { ...actual, isMock: false };
});

// ModelBadge has its own query and is not under test; the tour is a separate concern.
vi.mock("../components/ModelBadge", () => ({ ModelBadge: () => null }));
vi.mock("./OnboardingTour", () => ({ default: () => null }));

import { Shell } from "./Shell";
import { EvaluatePage } from "../features/evaluate/EvaluatePage";
import { isMock } from "@/api/mock";
import { renderWithQuery } from "@/test/utils";

const HEALTH_URL = "/api/v1/health";
const HEALTH_KEY = ["service-health"];

const HEALTHY = {
  status: "healthy",
  model_provenance: { source: "seed", model_version: "20260712_200332" },
  checks: {
    models: { status: "healthy", loaded: true, neural_network_loaded: false, xgboost_loaded: true },
  },
};

const jsonResponse = (payload: unknown, status = 200) =>
  new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } });

/** One answer for the health request: a body, an HTTP status, or a dropped connection. */
type Step = { json: unknown } | { status: number } | { error: Error };

/**
 * Answers GET /api/v1/health with the steps in order, repeating the last one.
 * Every other URL gets an empty object, so the Evaluate page can mount beside
 * the shell without its own queries failing.
 */
function healthSteps(...steps: Step[]) {
  let call = 0;
  const stub = vi.fn(async (input: RequestInfo | URL) => {
    if (String(input) !== HEALTH_URL) return jsonResponse({});
    const step = steps[Math.min(call, steps.length - 1)];
    call += 1;
    if ("error" in step) throw step.error;
    if ("status" in step) return new Response("upstream said no", { status: step.status });
    return jsonResponse(step.json);
  });
  globalThis.fetch = stub as unknown as typeof fetch;
  return stub;
}

const renderShell = () =>
  renderWithQuery(
    <MemoryRouter initialEntries={["/"]}>
      <Shell />
    </MemoryRouter>,
  );

/** The header pill, read from the DOM. The class is checked alongside the text. */
function headerPill(): HTMLElement {
  const pill = document.querySelector<HTMLElement>(".shell-top .status-pill");
  if (!pill) throw new Error("no status pill in the header");
  return pill;
}

/**
 * Waits until the health query has an answer and nothing is in flight. Waiting
 * for the request alone is not enough: the pill reads Unknown before any answer
 * arrives, so a check made too early would pass for the wrong reason.
 */
async function settled(queryClient: QueryClient) {
  await waitFor(() => expect(queryClient.getQueryState(HEALTH_KEY)?.status).not.toBe("pending"));
  await waitFor(() => expect(queryClient.isFetching()).toBe(0));
}

describe("the guard on these tests", () => {
  it("really is in production mode", () => {
    expect(isMock).toBe(false);
  });
});

describe("the header pill reports the service's health", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("reads Operational for a healthy service, with the Operational class", async () => {
    healthSteps({ json: HEALTHY });
    const { queryClient } = renderShell();
    await settled(queryClient);

    expect(headerPill().textContent).toBe("Operational");
    expect(headerPill().className).toBe("status-pill");
  });

  it.each([["degraded"], ["unhealthy"]])("reads Degraded for the status %j", async (status) => {
    healthSteps({ json: { ...HEALTHY, status } });
    const { queryClient } = renderShell();
    await settled(queryClient);

    expect(headerPill().textContent).toBe("Degraded");
    expect(headerPill().classList.contains("is-degraded")).toBe(true);
    expect(screen.queryByText("Operational")).toBeNull();
  });

  it("reads Unknown when the health request fails with a status", async () => {
    healthSteps({ status: 503 });
    const { queryClient } = renderShell();
    await settled(queryClient);

    expect(queryClient.getQueryState(HEALTH_KEY)?.status).toBe("error");
    expect(headerPill().textContent).toBe("Unknown");
    expect(headerPill().classList.contains("is-unknown")).toBe(true);
  });

  it("reads Unknown when the connection drops", async () => {
    healthSteps({ error: new TypeError("Failed to fetch") });
    const { queryClient } = renderShell();
    await settled(queryClient);

    expect(headerPill().textContent).toBe("Unknown");
  });

  it.each([["an empty object", {}], ["an empty status", { status: "" }], ["a blank status", { status: "   " }]])(
    "reads Unknown for %s",
    async (_case, body) => {
      healthSteps({ json: body });
      const { queryClient } = renderShell();
      await settled(queryClient);

      expect(headerPill().textContent).toBe("Unknown");
      expect(screen.queryByText("Operational")).toBeNull();
    },
  );

  it("reads Unknown after a refetch fails, though the last good answer is still cached", async () => {
    const stub = healthSteps({ json: HEALTHY }, { status: 500 });
    const { queryClient } = renderShell();
    await settled(queryClient);
    expect(headerPill().textContent).toBe("Operational");

    await queryClient.refetchQueries({ queryKey: HEALTH_KEY });
    await waitFor(() => expect(queryClient.getQueryState(HEALTH_KEY)?.status).toBe("error"));

    // The old data is still there, which is exactly why isError is the test.
    expect(queryClient.getQueryData(HEALTH_KEY)).toEqual(HEALTHY);
    expect(stub).toHaveBeenCalledTimes(2);
    expect(headerPill().textContent).toBe("Unknown");
    expect(screen.queryByText("Operational")).toBeNull();
  });

  it("reads Operational again once the service recovers", async () => {
    healthSteps({ json: HEALTHY }, { status: 500 }, { json: HEALTHY });
    const { queryClient } = renderShell();
    await settled(queryClient);

    await queryClient.refetchQueries({ queryKey: HEALTH_KEY });
    await waitFor(() => expect(headerPill().textContent).toBe("Unknown"));

    await queryClient.refetchQueries({ queryKey: HEALTH_KEY });
    await waitFor(() => expect(headerPill().textContent).toBe("Operational"));
    expect(headerPill().classList.contains("is-unknown")).toBe(false);
  });

  it.each([
    ["a 500", () => healthSteps({ status: 500 })],
    ["a dropped connection", () => healthSteps({ error: new TypeError("Failed to fetch") })],
    ["an empty object", () => healthSteps({ json: {} })],
    ["a degraded status", () => healthSteps({ json: { ...HEALTHY, status: "degraded" } })],
  ])("never shows a literal Operational for %s", async (_case, arrange) => {
    arrange();
    const { queryClient } = renderShell();
    await settled(queryClient);

    expect(screen.queryByText("Operational")).toBeNull();
  });
});

describe("the header pill and the Evaluate hero share one health request", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("makes one call to /health with both mounted, and both read the same answer", async () => {
    const stub = healthSteps({ json: HEALTHY });
    const { queryClient } = renderWithQuery(
      <MemoryRouter initialEntries={["/evaluate"]}>
        <Routes>
          <Route element={<Shell />}>
            <Route path="/evaluate" element={<EvaluatePage />} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
    await settled(queryClient);

    const healthCalls = stub.mock.calls.filter(([input]) => String(input) === HEALTH_URL);
    expect(healthCalls).toHaveLength(1);
    expect(screen.getAllByText("Operational")).toHaveLength(2);
  });
});
