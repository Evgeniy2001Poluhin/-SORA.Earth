import { describe, expect, it, vi, beforeEach } from "vitest";
import { act, screen, waitFor } from "@testing-library/react";

import { WhatIf } from "./WhatIf";
import { evaluateApi } from "@/api/endpoints/evaluate";
import { renderWithQuery, stubChartLayout } from "@/test/utils";

const FORM = {
  project_name: "Solar Farm",
  country: "Sweden",
  budget_usd: 150000,
  co2_reduction_tons_per_year: 120,
  social_impact_score: 8,
  project_duration_months: 24,
};

describe("WhatIf request payload", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    stubChartLayout();
  });

  it("falls back to the form when there is no previous run", async () => {
    const spy = vi.spyOn(evaluateApi, "whatIf");
    // EvaluatePage builds lastRun by spreading a possibly-absent run, so it can
    // arrive carrying only total_score and country.
    renderWithQuery(<WhatIf form={FORM} lastRun={{ country: "Sweden", total_score: 71 }} />);

    await act(async () => { await vi.advanceTimersByTimeAsync(500); });

    expect(spy).toHaveBeenCalledTimes(1);
    const body = spy.mock.calls[0][0];
    expect(body.budget).toBe(FORM.budget_usd);
    expect(body.co2_reduction).toBe(FORM.co2_reduction_tons_per_year);
    expect(body.social_impact).toBe(FORM.social_impact_score);
    expect(body.duration_months).toBe(FORM.project_duration_months);
  });

  it("sends backend field names only", async () => {
    const spy = vi.spyOn(evaluateApi, "whatIf");
    renderWithQuery(<WhatIf form={FORM} lastRun={null} />);

    await act(async () => { await vi.advanceTimersByTimeAsync(500); });

    const body = spy.mock.calls[0][0] as Record<string, unknown>;
    expect(Object.keys(body).sort()).toEqual(
      ["budget", "co2_reduction", "duration_months", "region", "social_impact"],
    );
    expect(body).not.toHaveProperty("budget_usd");
    expect(body).not.toHaveProperty("social_impact_score");
  });

  it("keeps a legitimate 0 instead of falling back", async () => {
    const spy = vi.spyOn(evaluateApi, "whatIf");
    renderWithQuery(
      <WhatIf form={FORM} lastRun={{ ...FORM, co2_reduction_tons_per_year: 0, social_impact_score: 0 }} />,
    );

    await act(async () => { await vi.advanceTimersByTimeAsync(500); });

    const body = spy.mock.calls[0][0];
    expect(body.co2_reduction).toBe(0);
    expect(body.social_impact).toBe(0);
  });

  it.each([
    ["NaN", Number.NaN],
    ["Infinity", Number.POSITIVE_INFINITY],
    ["-Infinity", Number.NEGATIVE_INFINITY],
  ])("does not fire the request when budget is %s", async (_label, value) => {
    const spy = vi.spyOn(evaluateApi, "whatIf");
    renderWithQuery(<WhatIf form={{ ...FORM, budget_usd: value }} lastRun={null} />);

    await act(async () => { await vi.advanceTimersByTimeAsync(500); });

    expect(spy).not.toHaveBeenCalled();
  });

  it("does not fire the request when a value is missing entirely", async () => {
    const spy = vi.spyOn(evaluateApi, "whatIf");
    const withoutBudget = { ...FORM, budget_usd: undefined };
    renderWithQuery(<WhatIf form={withoutBudget} lastRun={null} />);

    await act(async () => { await vi.advanceTimersByTimeAsync(500); });

    expect(spy).not.toHaveBeenCalled();
  });
});

describe("WhatIf rendering in mock mode", () => {
  beforeEach(() => stubChartLayout());

  it("renders the tornado from the mock variations", async () => {
    renderWithQuery(<WhatIf form={FORM} lastRun={null} />);

    expect(screen.getByText("What-If Sensitivity")).toBeInTheDocument();
    // The mock previously returned {scenarios}, which this view never reads.
    await waitFor(() => expect(screen.getByText(/Budget \+20%/)).toBeInTheDocument(), { timeout: 3000 });
  });
});
