import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ExplainPage } from "./ExplainPage";
import { explainApi } from "@/api/endpoints/explain";
import { renderWithQuery } from "@/test/utils";

describe("SHAP explain in mock mode", () => {
  it("returns top_contributions with the field names the page reads", async () => {
    const data = await explainApi.local({
      budget: 150000, co2_reduction: 120, social_impact: 8, duration_months: 24,
    });

    // The mock previously returned features/name/shap, none of which this page reads.
    expect(Array.isArray(data.top_contributions)).toBe(true);
    expect(data.top_contributions.length).toBeGreaterThan(0);
    for (const c of data.top_contributions) {
      expect(typeof c.feature).toBe("string");
      expect(Number.isFinite(c.shap_value)).toBe(true);
      expect(Number.isFinite(c.value)).toBe(true);
    }
  });

  it("echoes the caller's inputs rather than hard-coded defaults", async () => {
    const data = await explainApi.local({
      budget: 999000, co2_reduction: 777, social_impact: 3, duration_months: 9,
    });
    const byFeature = Object.fromEntries(data.top_contributions.map((c) => [c.feature, c.value]));

    expect(byFeature.budget).toBe(999000);
    expect(byFeature.co2_reduction).toBe(777);
    expect(byFeature.social_impact).toBe(3);
    expect(byFeature.duration_months).toBe(9);
  });

  it("renders the waterfall after picking a preset", async () => {
    const user = userEvent.setup();
    renderWithQuery(<ExplainPage />);

    expect(screen.getByText("Why this prediction?")).toBeInTheDocument();

    expect(screen.getByText("No data yet")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "MED" }));

    // Rows are keyed by contribution.feature, so this only renders if the mock
    // returns top_contributions with a feature field.
    await waitFor(() => expect(screen.getByText("co2_reduction")).toBeInTheDocument(), {
      timeout: 3000,
    });
    expect(screen.getByText("social_impact")).toBeInTheDocument();
  });
});
