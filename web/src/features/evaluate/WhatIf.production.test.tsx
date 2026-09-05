/**
 * WhatIf against the production path (#218).
 *
 * The mock-mode block in `WhatIf.test.tsx` covers the tornado rendering; the
 * payload block above it stubs `evaluateApi.whatIf` directly and is
 * mode-independent. Neither exercises the branch that runs on the deployed
 * site.
 *
 * The failure this guards is specific to a sensitivity view: a tornado drawn
 * from nothing still looks like an answer. `tornado` falls back to
 * `?? 0` per row, so the empty-state check below is what keeps a failed
 * request from rendering three bars all reading "no effect" as if measured.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";

vi.mock("@/api/mock", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/mock")>();
  return { ...actual, isMock: false };
});

import { WhatIf } from "./WhatIf";
import { evaluateApi } from "@/api/endpoints/evaluate";
import { isMock } from "@/api/mock";
import { renderWithQuery, stubChartLayout } from "@/test/utils";
import { callsOf, scoreLikeNumbersIn, stubJson, stubStatus, stubTransportError } from "@/test/http";

const FORM = {
  project_name: "Solar Farm",
  country: "Sweden",
  budget_usd: 150000,
  co2_reduction_tons_per_year: 120,
  social_impact_score: 8,
  project_duration_months: 24,
};

const variation = (change: number) => ({
  new_value: 1,
  new_score: 50 + change,
  score_change: change,
  new_probability: 0.5,
  prob_change: 0,
});

/** Score changes no canned run produces, and ordered so the tornado's sort
 *  is observable: CO2 must come first. */
const SERVER_WHATIF = {
  base: {},
  variations: {
    budget: variation(1.5),
    co2_reduction: variation(9.25),
    social_impact: variation(-3.75),
  },
};

describe("the guard on these tests", () => {
  it("really is in production mode", () => {
    expect(isMock).toBe(false);
  });
});

describe("WhatIf on the production path", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    stubChartLayout();
  });

  it("asks the what-if endpoint rather than answering from canned data", async () => {
    const fetchStub = stubJson(SERVER_WHATIF);

    await evaluateApi.whatIf({ budget: 150000, co2_reduction: 120, social_impact: 8 });

    const [call] = callsOf(fetchStub);
    expect(call.url).toBe("/api/v1/what-if");
    expect(call.method).toBe("POST");
  });

  it("returns the server's variations, unaltered", async () => {
    stubJson(SERVER_WHATIF);

    const data = await evaluateApi.whatIf({ budget: 150000, co2_reduction: 120, social_impact: 8 });

    expect(data.variations.co2_reduction.score_change).toBe(9.25);
    expect(data.variations.social_impact.score_change).toBe(-3.75);
  });

  it("renders a tornado ordered by the server's magnitudes", async () => {
    stubJson(SERVER_WHATIF);

    renderWithQuery(<WhatIf form={FORM} lastRun={FORM} />);

    await waitFor(() => expect(screen.getByText("CO2 +20%")).toBeInTheDocument(), {
      timeout: 3000,
    });
    // Rows exist for all three; ordering is by |score_change|, so CO2 (9.25)
    // precedes Social (3.75) precedes Budget (1.5).
    const labels = screen.getAllByText(/Budget \+20%|CO2 \+20%|Social \+1/).map((n) => n.textContent);
    expect(labels).toEqual(["CO2 +20%", "Social +1", "Budget +20%"]);
  });

  it("a 500 rejects instead of returning a sensitivity", async () => {
    stubStatus(500);
    await expect(
      evaluateApi.whatIf({ budget: 1, co2_reduction: 1, social_impact: 1 }),
    ).rejects.toThrow(/500/);
  });

  it("a dropped connection rejects instead of returning a sensitivity", async () => {
    stubTransportError();
    await expect(
      evaluateApi.whatIf({ budget: 1, co2_reduction: 1, social_impact: 1 }),
    ).rejects.toThrow();
  });

  it("the rejection carries no number a caller could render", async () => {
    stubStatus(502);

    const error = await evaluateApi
      .whatIf({ budget: 1, co2_reduction: 1, social_impact: 1 })
      .catch((e: unknown) => e);

    expect(error).toBeInstanceOf(Error);
    expect(scoreLikeNumbersIn({ ...(error as Error) })).toEqual([]);
  });

  it("an empty variations map draws no bars rather than three reading +0.00", async () => {
    // #236. `if(!v) return []` guarded `undefined`; `{variations:{}}` is
    // truthy and each row fell back to `?? 0`, so the page drew three bars
    // claiming every parameter changes the score by exactly zero — a
    // sensitivity result nobody computed, presented as one.
    stubJson({ base: {}, variations: {} });

    const { container } = renderWithQuery(<WhatIf form={FORM} lastRun={FORM} />);

    await waitFor(
      () => expect(screen.getByText("Run an evaluation first")).toBeInTheDocument(),
      { timeout: 3000 },
    );
    expect(container.querySelectorAll(".wi-row")).toHaveLength(0);
    expect(container.textContent ?? "").not.toContain("+0.00");
  });

  it("a partial variations map draws only the rows the server answered", async () => {
    // Dropping the unanswered rows rather than zero-filling them: the two
    // the server did compute are real and must still be shown.
    stubJson({ base: {}, variations: { co2_reduction: variation(4.5), budget: variation(-1.25) } });

    const { container } = renderWithQuery(<WhatIf form={FORM} lastRun={FORM} />);

    await waitFor(() => expect(container.querySelectorAll(".wi-row")).toHaveLength(2), {
      timeout: 3000,
    });
    expect(screen.getByText("CO2 +20%")).toBeInTheDocument();
    expect(screen.getByText("Budget +20%")).toBeInTheDocument();
    expect(screen.queryByText("Social +1")).not.toBeInTheDocument();
  });

  it("draws no tornado at all when the request fails", async () => {
    // Not "draws zeros": `tornado` is [] without data, so the empty state
    // shows instead of three bars that would read as a measured "no effect".
    stubStatus(500);

    renderWithQuery(<WhatIf form={FORM} lastRun={FORM} />);

    await waitFor(
      () => expect(screen.getByText("Run an evaluation first")).toBeInTheDocument(),
      { timeout: 3000 },
    );
    expect(screen.queryByText("CO2 +20%")).not.toBeInTheDocument();
  });
});
