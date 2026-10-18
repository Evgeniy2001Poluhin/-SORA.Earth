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
import userEvent from "@testing-library/user-event";

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

/** A variation as /what-if answers it: `new_value` is what the server applied
 *  to the field it varied, and `score_change` is that change's effect. */
const variation = (newValue: number, change: number) => ({
  new_value: newValue,
  new_score: 50 + change,
  score_change: change,
  new_probability: 0.5,
  prob_change: 0,
});

/** Applied to FORM the way the server applies it: budget +20% is +30,000,
 *  CO2 is +20 tonnes a year (not a percentage), and social 8 is raised by 2
 *  to 10. Score changes no canned run produces, ordered so the tornado's sort
 *  is observable: CO2 must come first. */
const SERVER_WHATIF = {
  base: {},
  variations: {
    budget: variation(180000, 1.5),
    co2_reduction: variation(140, 9.25),
    social_impact: variation(10, -3.75),
  },
};

/** A /evaluate answer carrying only the score the sweep and baseline read. */
const scored = (total: number) =>
  ({ total_score: total }) as unknown as Awaited<ReturnType<typeof evaluateApi.evaluate>>;

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

    const { container } = renderWithQuery(<WhatIf form={FORM} lastRun={FORM} />);

    await waitFor(() => expect(screen.getByText("CO2 +20 t/yr")).toBeInTheDocument(), {
      timeout: 3000,
    });
    // Ordering is by |score_change|, so CO2 (9.25) precedes Social (3.75)
    // precedes Budget (1.5). Each label is the change the server applied.
    const labels = Array.from(container.querySelectorAll(".wi-lbl")).map((n) => n.textContent);
    expect(labels).toEqual(["CO2 +20 t/yr", "Social +2", "Budget +$30,000"]);
  });

  it("labels each row with the change the server applied, not a fixed description", async () => {
    // Social 4 raised by 2 to 6. A label written in advance said "Social +1",
    // which is what the code once said, not what was sent or returned.
    const social4 = { ...FORM, social_impact_score: 4 };
    stubJson({ base: {}, variations: { social_impact: variation(6, 2.5) } });

    const { container } = renderWithQuery(<WhatIf form={social4} lastRun={social4} />);

    await waitFor(() => expect(container.querySelectorAll(".wi-row")).toHaveLength(1), {
      timeout: 3000,
    });
    expect(screen.getByText("Social +2")).toBeInTheDocument();
    expect(screen.queryByText("Social +1")).not.toBeInTheDocument();
  });

  it("a score clamped at the top of the scale is labelled with the clamped change", async () => {
    // Social 9 cannot rise by 2: the server clamps it to 10, so the change it
    // applied is +1. The label follows the response, so it says +1 here.
    const social9 = { ...FORM, social_impact_score: 9 };
    stubJson({ base: {}, variations: { social_impact: variation(10, 0.5) } });

    const { container } = renderWithQuery(<WhatIf form={social9} lastRun={social9} />);

    await waitFor(() => expect(container.querySelectorAll(".wi-row")).toHaveLength(1), {
      timeout: 3000,
    });
    expect(screen.getByText("Social +1")).toBeInTheDocument();
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
    stubJson({
      base: {},
      variations: {
        co2_reduction: variation(140, 4.5),
        budget: variation(180000, -1.25),
      },
    });

    const { container } = renderWithQuery(<WhatIf form={FORM} lastRun={FORM} />);

    await waitFor(() => expect(container.querySelectorAll(".wi-row")).toHaveLength(2), {
      timeout: 3000,
    });
    // Only the two answered rows are labelled; the sweep's "Social (1-10)" tab
    // is a control, not a tornado row, so the labels are read from .wi-lbl.
    const labels = Array.from(container.querySelectorAll(".wi-lbl")).map((n) => n.textContent);
    expect(labels).toEqual(["CO2 +20 t/yr", "Budget +$30,000"]);
  });

  it("draws no tornado at all when the request fails", async () => {
    // Not "draws zeros": `tornado` is [] without data, so the empty state
    // shows instead of three bars that would read as a measured "no effect".
    stubStatus(500);

    const { container } = renderWithQuery(<WhatIf form={FORM} lastRun={FORM} />);

    await waitFor(
      () => expect(screen.getByText("Run an evaluation first")).toBeInTheDocument(),
      { timeout: 3000 },
    );
    expect(container.querySelectorAll(".wi-row")).toHaveLength(0);
  });
});

describe("the baseline dot on the production path", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    stubChartLayout();
  });

  /** Runs the live sweep and waits for its line to be drawn. */
  async function runSweep(container: HTMLElement) {
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Run sweep" }));
    await waitFor(() => expect(container.querySelector(".recharts-line")).not.toBeNull(), {
      timeout: 3000,
    });
  }

  it("draws no baseline dot when no run has been scored, rather than one at 50", async () => {
    // The old fallback was `base?.total_score ?? 50`: a dot at 50 presented as
    // the baseline score, with no evaluation behind it.
    vi.spyOn(evaluateApi, "evaluate").mockResolvedValue(scored(60));
    stubJson(SERVER_WHATIF);

    const { container } = renderWithQuery(<WhatIf form={FORM} lastRun={null} />);
    await runSweep(container);

    expect(container.querySelector(".recharts-reference-dot")).toBeNull();
  });

  it("draws the baseline dot when the base run carries its own score", async () => {
    // A real value still renders: the dot is there when the score is.
    vi.spyOn(evaluateApi, "evaluate").mockResolvedValue(scored(60));
    stubJson(SERVER_WHATIF);

    const { container } = renderWithQuery(<WhatIf form={FORM} lastRun={{ ...FORM, total_score: 71 }} />);
    await runSweep(container);

    const dot = container.querySelector(".recharts-reference-dot circle");
    expect(dot).not.toBeNull();
    // Placed by its score, not at a constant: the sweep is scored at 60 and the
    // baseline at 71, so the dot must sit higher on the chart (smaller cy).
    const sweepCy = Number(container.querySelector(".recharts-line-dot")?.getAttribute("cy"));
    expect(Number(dot?.getAttribute("cy"))).toBeLessThan(sweepCy);
  });
});
