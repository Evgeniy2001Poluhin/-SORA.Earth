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
  it("gets votes_for from the uncertainty mock", async () => {
    const data = await calibrationApi.uncertainty({
      budget: 150000, co2_reduction: 120, social_impact: 8, duration_months: 24,
    });

    // The card reads tree_distribution.votes_for to show the tree vote.
    expect(Number.isFinite(data.tree_distribution.votes_for)).toBe(true);
    expect(data.tree_distribution.votes_for).toBeGreaterThanOrEqual(0);
    expect(data.tree_distribution.votes_for).toBeLessThanOrEqual(data.tree_distribution.n_trees);
  });

  it("renders the mean probability and tree vote", async () => {
    renderWithQuery(<UncertaintyCard payload={PAYLOAD} />);

    // The card shows "{votes_for} of {n_trees} trees vote for success".
    await waitFor(() => expect(screen.getByText(/trees vote for success/)).toBeInTheDocument(), { timeout: 3000 });
    // The mean probability is shown as a percentage.
    expect(screen.getByText(/72\.3%/)).toBeInTheDocument();
  });
});
