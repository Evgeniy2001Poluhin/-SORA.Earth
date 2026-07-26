import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";

import CountryRanking from "./CountryRanking";
import { evaluateApi } from "@/api/endpoints/evaluate";
import { renderWithQuery } from "@/test/utils";

const REQUEST = {
  project_name: "Wind Park",
  country: "Germany",
  budget_usd: 220000,
  co2_reduction_tons_per_year: 300,
  social_impact_score: 7,
  project_duration_months: 18,
};

describe("CountryRanking in mock mode", () => {
  it("renders rows from the mock ranking payload", async () => {
    const data = await evaluateApi.ranking(REQUEST);

    renderWithQuery(
      <CountryRanking data={data} loading={false} currentCountry="Germany" onPickCountry={vi.fn()} />,
    );

    // The component reads data.ranking; the mock used to return rank/percentile/peers.
    expect(data.ranking.length).toBeGreaterThan(0);
    expect(screen.getByText("Germany")).toBeInTheDocument();
  });

  it("returns entries carrying the score fields the table reads", async () => {
    const data = await evaluateApi.ranking(REQUEST);

    expect(data.count).toBe(data.ranking.length);
    for (const row of data.ranking) {
      expect(typeof row.country).toBe("string");
      expect(Number.isFinite(row.total_score)).toBe(true);
    }
  });

  it("renders without rows when data is absent", () => {
    renderWithQuery(
      <CountryRanking data={undefined} loading={false} currentCountry="Germany" onPickCountry={vi.fn()} />,
    );
    expect(document.body.textContent).not.toBe("");
  });
});
