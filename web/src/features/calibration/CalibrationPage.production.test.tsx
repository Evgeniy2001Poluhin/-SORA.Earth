/**
 * CalibrationPage on the production path (F028 to F030).
 *
 * The page shows the three models' success probabilities beside their
 * consensus. Those are the same model probabilities the Evaluate page labels
 * unvalidated, and the tree interval is a spread of tree votes, not an
 * interval for the outcome. These tests read the rendered page, so the labels
 * are asserted where a reader sees them.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("@/api/mock", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/mock")>();
  return { ...actual, isMock: false };
});

import { CalibrationPage } from "./CalibrationPage";
import { isMock } from "@/api/mock";
import { SUCCESS_PROBABILITY_NOTE } from "../evaluate/successProbabilityNote";
import { renderWithQuery, stubChartLayout } from "@/test/utils";
import { stubJson } from "@/test/http";

const DISCREPANCY = {
  models: {
    rf_v1: { proba: 0.712, weight: 0.34 },
    stacking_v2: { proba: 0.723, weight: 0.33 },
    calibrated_v2: { proba: 0.72, weight: 0.33, near_deterministic: false },
  },
  consensus: { weighted_proba: 0.718, method: "weighted_avg" },
  divergence: { max_spread: 0.011, std: 0.006, max_pair: ["rf_v1", "stacking_v2"] },
  tree_uncertainty: { std: 0.031, ci_90: [0.672, 0.774], n_trees: 100 },
  recommendation: "consensus",
};

describe("the guard on these tests", () => {
  it("really is in production mode", () => {
    expect(isMock).toBe(false);
  });
});

describe("CalibrationPage on the production path", () => {
  beforeEach(() => {
    stubChartLayout();
  });

  it("labels the weighted probability unvalidated, with the shared note on the figure", async () => {
    stubJson(DISCREPANCY);
    const user = userEvent.setup();
    renderWithQuery(<CalibrationPage />);

    await user.click(screen.getByRole("button", { name: "MED" }));

    const figure = await screen.findByText("71.8%");
    expect(figure).toHaveAttribute("title", SUCCESS_PROBABILITY_NOTE);
    expect(figure).toHaveTextContent("unvalidated");
  });

  it("labels every per-model probability with the same note, and the section unvalidated", async () => {
    stubJson(DISCREPANCY);
    const user = userEvent.setup();
    renderWithQuery(<CalibrationPage />);

    await user.click(screen.getByRole("button", { name: "MED" }));

    expect(await screen.findByText("proba 71.20%")).toHaveAttribute("title", SUCCESS_PROBABILITY_NOTE);
    expect(screen.getByText("proba 72.30%")).toHaveAttribute("title", SUCCESS_PROBABILITY_NOTE);
    expect(screen.getByText("proba 72.00%")).toHaveAttribute("title", SUCCESS_PROBABILITY_NOTE);
    expect(screen.getByText(/Per-model probability/)).toHaveTextContent("unvalidated");
  });

  it("calls the tree range a spread, not a confidence interval for the outcome", async () => {
    stubJson(DISCREPANCY);
    const user = userEvent.setup();
    const { container } = renderWithQuery(<CalibrationPage />);

    await user.click(screen.getByRole("button", { name: "MED" }));

    expect(await screen.findByText("Tree spread (5–95%)")).toBeInTheDocument();
    expect(screen.getByText("not a confidence interval for the outcome")).toBeInTheDocument();
    expect(container.textContent ?? "").not.toContain("Tree CI 90%");
  });
});
