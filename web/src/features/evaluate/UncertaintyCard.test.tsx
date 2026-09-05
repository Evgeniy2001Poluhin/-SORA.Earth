import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";

// Mock mode for this file, said here rather than inherited (#218). These assert that p5/p95 line up with lower_90/upper_90 in the canned
// payload -- a contract the type omitted until it was checked against
// app/api/calibration.py.
// The production-mode counterpart is UncertaintyCard.production.test.tsx.
vi.mock("@/api/mock", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/mock")>();
  return { ...actual, isMock: true };
});

import { UncertaintyCard } from "./UncertaintyCard";
import { calibrationApi } from "@/api/endpoints/calibration";
import { renderWithQuery } from "@/test/utils";

const PAYLOAD = {
  budget_usd: 150000,
  co2_reduction_tons_per_year: 120,
  social_impact_score: 8,
  project_duration_months: 24,
};

describe("UncertaintyCard in mock mode", () => {
  it("gets p5 and p95 from the uncertainty mock", async () => {
    const data = await calibrationApi.uncertainty({
      budget: 150000, co2_reduction: 120, social_impact: 8, duration_months: 24,
    });

    // The card reads tree_distribution.p5 / .p95 directly; the type omitted
    // both until the contract was checked against app/api/calibration.py.
    expect(Number.isFinite(data.tree_distribution.p5)).toBe(true);
    expect(Number.isFinite(data.tree_distribution.p95)).toBe(true);
    expect(data.tree_distribution.p5).toBeLessThanOrEqual(data.tree_distribution.p95);
    expect(data.tree_distribution.p5).toBe(data.prediction.lower_90);
    expect(data.tree_distribution.p95).toBe(data.prediction.upper_90);
  });

  it("renders the 5-95% band that p5/p95 feed", async () => {
    renderWithQuery(<UncertaintyCard payload={PAYLOAD} />);

    // "5-95%" is the label over the tree_distribution.p5 / .p95 range.
    await waitFor(() => expect(screen.getByText("5-95%")).toBeInTheDocument(), { timeout: 3000 });
    expect(screen.getByText("trees")).toBeInTheDocument();
    expect(screen.getByText("std")).toBeInTheDocument();
  });
});
