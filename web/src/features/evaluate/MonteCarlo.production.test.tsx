/**
 * MonteCarlo against the production path (#218).
 *
 * The mock-mode file next to this one asserts that the canned histogram is
 * shaped the way the component's bar sizing needs. This one asserts the
 * branch that runs on the deployed site, and that a failed simulation is
 * reported as a failure rather than as a distribution.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";

vi.mock("@/api/mock", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/mock")>();
  return { ...actual, isMock: false };
});

import MonteCarlo from "./MonteCarlo";
import { evaluateApi } from "@/api/endpoints/evaluate";
import { isMock } from "@/api/mock";
import { renderWithQuery, stubChartLayout } from "@/test/utils";
import { callsOf, scoreLikeNumbersIn, stubJson, stubStatus, stubTransportError } from "@/test/http";

const REQUEST = {
  project_name: "Solar Farm",
  country: "Sweden",
  budget_usd: 150000,
  co2_reduction_tons_per_year: 340,
  social_impact_score: 9,
  project_duration_months: 18,
};

/** A distribution no mock would produce: n=7, and percentiles nothing like
 *  the canned run's. A test that passes on this and on the mock is not
 *  distinguishing them. */
const SERVER_RUN = {
  n: 7,
  mean: 13.5,
  stdev: 1.25,
  min: 11,
  max: 16,
  // Every value distinct, so an assertion names one field rather than
  // matching whichever of several happens to share a rendering.
  p10: 11.5,
  p50: 14.7,
  p90: 15.9,
  histogram: { edges: [11, 12, 13, 14, 15, 16], counts: [1, 1, 2, 2, 1] },
};

describe("the guard on these tests", () => {
  it("really is in production mode", () => {
    expect(isMock).toBe(false);
  });
});

describe("MonteCarlo on the production path", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    stubChartLayout();
  });

  it("asks the monte-carlo endpoint rather than simulating locally", async () => {
    const fetchStub = stubJson(SERVER_RUN);

    await evaluateApi.monteCarlo(REQUEST);

    const [call] = callsOf(fetchStub);
    expect(call.url).toBe("/api/v1/evaluate/monte-carlo");
    expect(call.method).toBe("POST");
  });

  it("returns the server's distribution, unaltered", async () => {
    stubJson(SERVER_RUN);

    const data = await evaluateApi.monteCarlo(REQUEST);

    expect(data).toEqual(SERVER_RUN);
    // The mock always runs 1000 draws; the server here ran 7. If this reads
    // 1000, the canned path answered.
    expect(data.n).toBe(7);
  });

  it("renders the server's numbers", async () => {
    stubJson(SERVER_RUN);
    const data = await evaluateApi.monteCarlo(REQUEST);

    renderWithQuery(<MonteCarlo data={data} loading={false} onRun={vi.fn()} />);

    expect(screen.getByText("7")).toBeInTheDocument();       // n
    expect(screen.getByText("13.5")).toBeInTheDocument();    // mean
    expect(screen.getByText("1.25")).toBeInTheDocument();    // stdev
    expect(screen.getByText("14.7")).toBeInTheDocument();    // p50
  });

  it("a 500 rejects instead of returning a distribution", async () => {
    stubStatus(500);
    await expect(evaluateApi.monteCarlo(REQUEST)).rejects.toThrow(/500/);
  });

  it("a dropped connection rejects instead of returning a distribution", async () => {
    stubTransportError();
    await expect(evaluateApi.monteCarlo(REQUEST)).rejects.toThrow();
  });

  it("the rejection carries no number a caller could render", async () => {
    stubStatus(502);

    const error = await evaluateApi.monteCarlo(REQUEST).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(Error);
    expect(scoreLikeNumbersIn({ ...(error as Error) })).toEqual([]);
  });

  it("an empty answer stays empty rather than being filled in", async () => {
    stubJson({});

    const data = await evaluateApi.monteCarlo(REQUEST);

    expect(data).toEqual({});
    expect(scoreLikeNumbersIn(data)).toEqual([]);
  });
});
