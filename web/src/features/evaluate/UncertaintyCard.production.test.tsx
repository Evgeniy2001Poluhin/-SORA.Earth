/**
 * UncertaintyCard against the production path (#218).
 *
 * Unlike CountryRanking and MonteCarlo, this component fetches for itself, so
 * the failure half of the contract can be asserted on the rendered page and
 * not only on the endpoint: a failing API must leave nothing on screen that
 * reads as a measured interval.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";

vi.mock("@/api/mock", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/mock")>();
  return { ...actual, isMock: false };
});

import { UncertaintyCard } from "./UncertaintyCard";
import { calibrationApi } from "@/api/endpoints/calibration";
import { isMock } from "@/api/mock";
import { renderWithQuery } from "@/test/utils";
import { callsOf, scoreLikeNumbersIn, stubJson, stubStatus, stubTransportError } from "@/test/http";

const PAYLOAD = {
  budget_usd: 150000,
  co2_reduction_tons_per_year: 120,
  social_impact_score: 8,
  project_duration_months: 24,
};

/** Distinct from the canned UNC constant in calibration.ts: 0.5/0.6/0.7
 *  rather than 0.672/0.720/0.774, so canned data answering instead of this
 *  is visible in the assertion rather than plausible. */
const SERVER_UNCERTAINTY = {
  probability: 60,
  prediction: { mean: 0.6, median: 0.6, lower_90: 0.5, upper_90: 0.7 },
  tree_distribution: { std: 0.02, n_trees: 33, min: 0.45, max: 0.75, p5: 0.5, p95: 0.7 },
  confidence: "medium",
  uncertainty: { method: "RF tree variance", mean: 60, std: 2, ci_90: [50, 70], n_trees: 33 },
  reliability: "medium",
};

describe("the guard on these tests", () => {
  it("really is in production mode", () => {
    expect(isMock).toBe(false);
  });
});

describe("UncertaintyCard on the production path", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("asks the uncertainty endpoint rather than answering from canned data", async () => {
    const fetchStub = stubJson(SERVER_UNCERTAINTY);

    await calibrationApi.uncertainty({
      budget: 150000, co2_reduction: 120, social_impact: 8, duration_months: 24,
    });

    const [call] = callsOf(fetchStub);
    expect(call.url).toBe("/api/v1/predict/uncertainty");
    expect(call.method).toBe("POST");
  });

  it("renders the server's interval, not the canned one", async () => {
    stubJson(SERVER_UNCERTAINTY);

    renderWithQuery(<UncertaintyCard payload={PAYLOAD} />);

    // 33 trees, not the canned 100.
    await waitFor(() => expect(screen.getByText("33")).toBeInTheDocument(), { timeout: 3000 });
    expect(screen.getByText("50.0-70.0%")).toBeInTheDocument();
    expect(screen.getByText("MEDIUM CONFIDENCE")).toBeInTheDocument();
  });

  it("a 500 rejects instead of returning an interval", async () => {
    stubStatus(500);
    await expect(
      calibrationApi.uncertainty({ budget: 1, co2_reduction: 1, social_impact: 1, duration_months: 1 }),
    ).rejects.toThrow(/500/);
  });

  it("a dropped connection rejects instead of returning an interval", async () => {
    stubTransportError();
    await expect(
      calibrationApi.uncertainty({ budget: 1, co2_reduction: 1, social_impact: 1, duration_months: 1 }),
    ).rejects.toThrow();
  });

  it("the rejection carries no number a caller could render", async () => {
    stubStatus(503);

    const error = await calibrationApi
      .uncertainty({ budget: 1, co2_reduction: 1, social_impact: 1, duration_months: 1 })
      .catch((e: unknown) => e);

    expect(error).toBeInstanceOf(Error);
    expect(scoreLikeNumbersIn({ ...(error as Error) })).toEqual([]);
  });

  it("an empty 200 draws no interval rather than one reading 0.0%", async () => {
    // #236. This test was written first to pin the defect: `if (!q.data)`
    // guarded `undefined` while `{}` is truthy, and every read below was
    // `?? 0`, so a 200 carrying nothing painted a complete confidence
    // interval reading 0.0% — the #216 failure family, a page that looks
    // normal showing numbers the model never produced. The guard now asks
    // for the fields it renders, and this asserts the fix.
    stubJson({});

    const { container } = renderWithQuery(<UncertaintyCard payload={PAYLOAD} />);

    // Give the query time to resolve, so this cannot pass merely by being
    // asserted before the empty answer arrived.
    await new Promise((resolve) => setTimeout(resolve, 300));

    expect(container.querySelector(".uncertainty-card")).toBeNull();
    expect(container.textContent ?? "").not.toContain("0.0%");
  });

  it("a partial 200 missing tree_distribution still draws nothing", async () => {
    // Half an answer is not an answer: the card reads from both
    // `prediction` and `tree_distribution`, so either being absent has to
    // mean the same thing as neither arriving.
    stubJson({ prediction: { mean: 0.6, median: 0.6, lower_90: 0.5, upper_90: 0.7 } });

    const { container } = renderWithQuery(<UncertaintyCard payload={PAYLOAD} />);
    await new Promise((resolve) => setTimeout(resolve, 300));

    expect(container.querySelector(".uncertainty-card")).toBeNull();
  });

  it("renders no interval at all when the request fails", async () => {
    // The component's half: `if (!q.data) return null`. A failed request must
    // leave nothing on screen that reads as a measured confidence interval.
    stubStatus(500);

    const { container } = renderWithQuery(<UncertaintyCard payload={PAYLOAD} />);

    await waitFor(() => expect(container.querySelector(".uncertainty-card")).toBeNull(), {
      timeout: 3000,
    });
    expect(container.textContent ?? "").not.toMatch(/%/);
  });
});
