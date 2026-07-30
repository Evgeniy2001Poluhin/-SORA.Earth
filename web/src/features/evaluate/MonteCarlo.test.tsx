import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";

import MonteCarlo from "./MonteCarlo";
import { evaluateApi } from "@/api/endpoints/evaluate";
import { renderWithQuery, stubChartLayout } from "@/test/utils";

const REQUEST = {
  project_name: "Solar Farm",
  country: "Sweden",
  budget_usd: 150000,
  co2_reduction_tons_per_year: 120,
  social_impact_score: 8,
  project_duration_months: 24,
};

describe("MonteCarlo in mock mode", () => {
  it("renders the stats the mock API actually returns", async () => {
    stubChartLayout();
    const data = await evaluateApi.monteCarlo(REQUEST);

    renderWithQuery(<MonteCarlo data={data} loading={false} onRun={vi.fn()} />);

    // These labels are driven by data.stdev / p10 / p90, the fields the mock
    // previously did not provide.
    expect(screen.getByText("STDEV")).toBeInTheDocument();
    expect(screen.getByText("P10")).toBeInTheDocument();
    expect(screen.getByText("P90")).toBeInTheDocument();
  });

  it("returns a histogram the component can size bars from", async () => {
    const data = await evaluateApi.monteCarlo(REQUEST);

    expect(data.histogram.counts.length).toBeGreaterThan(0);
    expect(data.histogram.edges.length).toBe(data.histogram.counts.length + 1);
    expect(data.histogram.counts.reduce((a, b) => a + b, 0)).toBe(data.n);
    expect(data.p10).toBeLessThanOrEqual(data.p50);
    expect(data.p50).toBeLessThanOrEqual(data.p90);
  });

  it("shows the loading state without data", () => {
    stubChartLayout();
    renderWithQuery(<MonteCarlo data={undefined} loading={true} onRun={vi.fn()} />);
    expect(document.body.textContent).not.toBe("");
  });
});
