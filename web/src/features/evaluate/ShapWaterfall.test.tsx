/**
 * The SHAP panel on the evaluate page, in the units /predict/explain answers.
 *
 * app/api/explain.py returns `probability` in percent (`round(proba * 100, 2)`)
 * but `base_value` and each `shap_value` as fractions of 1. The panel used to
 * print them side by side as they came -- `f(x) = 96.500` beside
 * `E[f(X)] = 0.602` -- so base + contributions never reached f(x) on screen.
 * The numbers below are the 2026-11-09 rehearsal's (Sweden, CO2 340).
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import type { ExplainResponse, ShapFeature } from "@/api/types";
import { ShapWaterfall } from "./EvaluatePage";

const f = (feature: string, shap_value: number): ShapFeature => ({
  feature, value: 0, shap_value,
  direction: shap_value > 0 ? "positive" : "negative", impact: "low",
});

const answer = (explanation: ShapFeature[]): ExplainResponse => ({
  prediction: 1, probability: 96.5, base_value: 0.602, explanation,
  all_features: [],
});

const nine = [
  f("budget_per_month", 0.216), f("budget", 0.158), f("co2_per_dollar", 0.037),
  f("duration_months", -0.034), f("efficiency_score", -0.026), f("co2_reduction", 0.008),
  f("social_impact", 0.004), f("year", 0.0), f("quarter", 0.0),
];

const percent = (text: string) => Number(text.replace(/[^\d.+-]/g, ""));

describe("ShapWaterfall", () => {
  it("shows f(x) and the base in the same unit as the headline estimate", () => {
    render(<ShapWaterfall data={answer(nine)} />);
    expect(screen.getByText("f(x) = 96.5%")).toBeInTheDocument();
    expect(screen.getByText("E[f(X)] = 60.2%")).toBeInTheDocument();
    expect(screen.queryByText(/0\.602/)).not.toBeInTheDocument();
    expect(screen.queryByText(/96\.500/)).not.toBeInTheDocument();
  });

  it("shows each contribution in percentage points", () => {
    render(<ShapWaterfall data={answer(nine)} />);
    expect(screen.getByText("+21.6 pp")).toBeInTheDocument();
    expect(screen.getByText("-3.4 pp")).toBeInTheDocument();
  });

  it("adds up: base plus every shown contribution is f(x), within rounding", () => {
    const { container } = render(<ShapWaterfall data={answer(nine)} />);
    const shown = [...container.querySelectorAll(".shap-val")].map(e => percent(e.textContent ?? ""));
    const rest = container.querySelector(".shap-rest")?.textContent ?? "";
    const restValue = rest ? percent(rest.split(":")[1]) : 0;
    const total = 60.2 + shown.reduce((s, v) => s + v, 0) + restValue;
    expect(Math.abs(total - 96.5)).toBeLessThanOrEqual(0.1 * (shown.length + 2));
  });

  it("names the features beyond the eight it draws instead of dropping them", () => {
    const { container } = render(<ShapWaterfall data={answer(nine)} />);
    expect(container.querySelectorAll(".shap-val")).toHaveLength(8);
    expect(container.querySelector(".shap-rest")?.textContent).toBe("1 other features: 0.0 pp");
  });
});
