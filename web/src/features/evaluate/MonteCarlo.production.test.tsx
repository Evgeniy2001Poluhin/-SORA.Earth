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

  // The test above checks the transport and stops there. It passed while the
  // component crashed on exactly that value, which is the gap #236 is about:
  // asserting what the client returns is not asserting what the page does
  // with it. These render it.

  it("renders the empty state rather than crashing on that same {}", () => {
    // MonteCarlo draws inside EvaluatePage, so this was a third way the page
    // blanked on an empty 200 -- `data.histogram.counts` on the Monte Carlo
    // tab, surviving the fixes to the result and explain paths.
    const { container } = renderWithQuery(
      <MonteCarlo data={{} as never} loading={false} onRun={() => {}} />,
    );

    expect(container.querySelector(".mc-empty")).not.toBeNull();
    expect(container.textContent ?? "").toContain("Click");
  });

  it("renders the empty state when the histogram is there but the stats are not", () => {
    const { container } = renderWithQuery(
      <MonteCarlo
        data={{ histogram: { counts: [1, 2], edges: [0, 1, 2] } } as never}
        loading={false}
        onRun={() => {}}
      />,
    );

    expect(container.querySelector(".mc-empty")).not.toBeNull();
    expect(container.textContent ?? "").not.toContain("NaN");
  });

  it("shows n out of requested when part of the sample failed", () => {
    // `n` counted successful runs while reading as the number asked for, so a
    // mean over 50 of 1000 samples looked like a mean over 50 of 50.
    const { container } = renderWithQuery(
      <MonteCarlo
        data={{
          status: "ok", requested: 1000, n: 50, failed: 950, reason_code: "partial_sample",
          mean: 61.25, stdev: 4.75, p10: 55.5, p50: 61.5, p90: 67.25, min: 48.5, max: 74.5,
          histogram: { counts: [2, 7, 3], edges: [48, 57, 66, 75] },
        } as never}
        loading={false}
        onRun={() => {}}
      />,
    );

    expect(container.textContent ?? "").toContain("50 / 1000");
  });

  it("shows n alone when every simulation succeeded", () => {
    const { container } = renderWithQuery(
      <MonteCarlo
        data={{
          status: "ok", requested: 500, n: 500, failed: 0, reason_code: null,
          mean: 61.25, stdev: 4.75, p10: 55.5, p50: 61.5, p90: 67.25, min: 48.5, max: 74.5,
          histogram: { counts: [2, 7, 3], edges: [48, 57, 66, 75] },
        } as never}
        loading={false}
        onRun={() => {}}
      />,
    );

    expect(container.textContent ?? "").not.toContain("500 / 500");
  });

  it("says the simulation failed rather than looking un-run", () => {
    // A 503 rejects, so `data` is absent -- the same state as "not run yet".
    // Without the flag the panel invited the user to press Run again on a
    // request that had just failed.
    const { container } = renderWithQuery(
      <MonteCarlo data={undefined} loading={false} failed onRun={() => {}} />,
    );

    expect(container.textContent ?? "").toContain("Simulation failed");
    expect(container.textContent ?? "").not.toContain("Click \"Run\"");
  });

  it("still invites a first run when nothing has been attempted", () => {
    const { container } = renderWithQuery(
      <MonteCarlo data={undefined} loading={false} onRun={() => {}} />,
    );

    expect(container.textContent ?? "").toContain("Click");
    expect(container.textContent ?? "").not.toContain("Simulation failed");
  });

  it("still draws a complete simulation the server did return", () => {
    const { container } = renderWithQuery(
      <MonteCarlo
        data={{
          n: 500, mean: 61.25, stdev: 4.75, p10: 55.5, p50: 61.5, p90: 67.25,
          min: 48.5, max: 74.5,
          histogram: { counts: [2, 7, 3], edges: [48, 57, 66, 75] },
        } as never}
        loading={false}
        onRun={() => {}}
      />,
    );

    expect(container.querySelector(".mc-empty")).toBeNull();
    expect(container.textContent ?? "").toContain("61.3");
  });
});
