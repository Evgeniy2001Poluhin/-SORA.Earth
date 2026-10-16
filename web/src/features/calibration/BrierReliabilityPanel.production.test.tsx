/**
 * BrierReliabilityPanel on the production path (F031, F032).
 *
 * The panel builds its own dataset in the browser and posts it to
 * /calibration/reliability. What it shows is a demonstration of how the
 * formulas behave, not a measurement of the deployed model, so these tests
 * pin three things: the title and disclaimer say so, the disclaimer sits
 * directly under the title, and a scenario sends the same dataset every time
 * it is pressed (the disclaimer calls that dataset fixed).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("@/api/mock", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/mock")>();
  return { ...actual, isMock: false };
});

import { BrierReliabilityPanel } from "./BrierReliabilityPanel";
import { isMock } from "@/api/mock";
import { renderWithQuery, stubChartLayout } from "@/test/utils";
import { callsOf, stubJson } from "@/test/http";

const TITLE = "Synthetic demonstration: how Brier and ECE work";
const DISCLAIMER =
  "These numbers come from a fixed illustrative dataset, not from the deployed model's predictions or any real outcomes.";

const RELIABILITY = {
  n_samples: 80,
  n_bins: 10,
  base_rate: 0.5,
  brier: 0.2,
  ece: 0.1,
  curve: {
    bin_lower: [0, 0.5],
    bin_upper: [0.5, 1],
    mean_predicted: [0.25, 0.75],
    mean_observed: [0.3, 0.7],
    count: [40, 40],
  },
  murphy: { reliability: 0.01, resolution: 0.09, uncertainty: 0.25 },
};

describe("the guard on these tests", () => {
  it("really is in production mode", () => {
    expect(isMock).toBe(false);
  });
});

describe("BrierReliabilityPanel on the production path", () => {
  beforeEach(() => {
    stubChartLayout();
  });

  it("is titled as a synthetic demonstration, and the old title is gone", () => {
    const { container } = renderWithQuery(<BrierReliabilityPanel />);

    expect(screen.getByRole("heading", { name: TITLE })).toBeInTheDocument();
    expect(screen.getByText(DISCLAIMER)).toBeInTheDocument();
    // "Calibration quality" read as a claim about the deployed model.
    expect(container.textContent ?? "").not.toContain("Calibration quality");
  });

  it("puts the disclaimer directly under the title", () => {
    renderWithQuery(<BrierReliabilityPanel />);

    const heading = screen.getByRole("heading", { name: TITLE });
    expect(heading.nextElementSibling).toHaveTextContent(DISCLAIMER);
  });

  it("sends the same synthetic dataset every time a scenario is pressed", async () => {
    const user = userEvent.setup();
    const stub = stubJson(RELIABILITY);
    renderWithQuery(<BrierReliabilityPanel />);
    const moderate = screen.getByRole("button", { name: "Moderate" });

    await user.click(moderate);
    await waitFor(() => expect(moderate).toBeEnabled());
    await user.click(moderate);
    await waitFor(() => expect(callsOf(stub)).toHaveLength(2));

    const [first, second] = callsOf(stub);
    expect(first.body).toEqual(second.body);
    expect((first.body as { probs: unknown[] }).probs).toHaveLength(80);
  });
});
