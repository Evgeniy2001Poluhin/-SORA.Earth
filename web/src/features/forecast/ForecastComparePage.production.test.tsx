/**
 * The model comparison when a model was trained without validation.
 *
 * `forecast_model_metrics` declares `test_samples`, `mae`, `rmse`, `mape` and
 * `r2_score` nullable -- "Number of test samples (if validation performed)" --
 * and the scheduler writes exactly such a row, with `status="success"`, when
 * the test split holds fewer than three points (app/scheduler.py:
 * `test_samples=len(test_df) if len(test_df) >= 3 else None`, and the four
 * errors likewise). `/forecast/metrics/latest` keeps success rows, so the page
 * receives it.
 *
 * Two things followed. `m.mae.toFixed(2)` on null threw and took the whole
 * comparison down -- on small data, which is precisely when a new or rebuilt
 * deployment has it. And `test_samples ?? 0` printed "24 / 0": zero test
 * samples, where the server said no validation was run. The R² warning quoted
 * the ensemble's count the same way, "(0 samples)", including when there is no
 * ensemble row at all.
 *
 * Measured with transport stubs, not deduced.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { waitFor } from "@testing-library/react";

vi.mock("@/api/mock", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/mock")>();
  return { ...actual, isMock: false };
});

import ForecastComparePage from "./ForecastComparePage";
import { isMock } from "@/api/mock";
import { renderWithQuery, stubChartLayout } from "@/test/utils";

const FORECAST = {
  history: [1, 2, 3].map((i) => ({ ds: `2026-09-0${i}`, y: 60 + i })),
  forecast: [4, 5].map((i) => ({ ds: `2026-09-0${i}`, yhat: 64 + i, yhat_lower: 60 + i, yhat_upper: 68 + i })),
  model: "ensemble", metric: "score", confidence: "medium", metadata: null,
};

const VALIDATED = {
  trained_at: "2026-09-20T03:00:00", mae: 2.45, rmse: 3.12, mape: 3.2, r2_score: 0.92,
  train_samples: 24, test_samples: 6, training_duration_sec: 1.5,
};
/** What the scheduler writes when the test split is under three points. */
const UNVALIDATED = {
  trained_at: "2026-09-20T03:00:00", mae: null, rmse: null, mape: null, r2_score: null,
  train_samples: 24, test_samples: null, training_duration_sec: 1.1,
};

const serve = (metrics: unknown) => {
  vi.spyOn(globalThis, "fetch").mockImplementation((async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const body = url.includes("/forecast/metrics/latest") ? metrics : FORECAST;
    return new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
  }) as typeof fetch);
};

/** The cells of the metrics-table row for one model. */
const rowCells = (c: HTMLElement, model: string): string[] | null => {
  const tr = Array.from(c.querySelectorAll("tbody tr")).find((r) => r.querySelector("td")?.textContent === model);
  return tr ? Array.from(tr.querySelectorAll("td")).map((td) => (td.textContent ?? "").trim()) : null;
};

describe("the guard on these tests", () => {
  it("really is in production mode", () => {
    expect(isMock).toBe(false);
  });
});

describe("ForecastComparePage metrics table", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders a model that was trained without validation instead of crashing", async () => {
    serve({ score: { ensemble: VALIDATED, prophet: UNVALIDATED } });
    const { container } = renderWithQuery(<ForecastComparePage />);
    await waitFor(() => expect(rowCells(container, "prophet")).not.toBeNull());
    const cells = rowCells(container, "prophet")!;
    // model, MAE, RMSE, MAPE, R², train / test, trained
    expect(cells.slice(1, 5)).toEqual(["—", "—", "—", "—"]);
  });

  it("does not report zero test samples where no validation was run", async () => {
    serve({ score: { ensemble: VALIDATED, prophet: UNVALIDATED } });
    const { container } = renderWithQuery(<ForecastComparePage />);
    await waitFor(() => expect(rowCells(container, "prophet")).not.toBeNull());
    expect(rowCells(container, "prophet")![5]).toBe("24 / —");
  });

  it("does not quote '0 samples' in the R² warning when the count is unknown", async () => {
    serve({ score: { prophet: { ...VALIDATED, r2_score: -0.4 } } });
    const { container } = renderWithQuery(<ForecastComparePage />);
    await waitFor(() => expect(container.textContent).toContain("Negative R²"));
    expect(container.textContent).not.toContain("(0 samples)");
  });

  // Controls: the real figures are still on screen.
  it("shows the validated model's figures", async () => {
    serve({ score: { ensemble: VALIDATED } });
    const { container } = renderWithQuery(<ForecastComparePage />);
    await waitFor(() => expect(rowCells(container, "ensemble")).not.toBeNull());
    const cells = rowCells(container, "ensemble")!;
    expect(cells.slice(1, 6)).toEqual(["2.45", "3.12", "3.20%", "0.92", "24 / 6"]);
  });

  it("still quotes the ensemble's sample count in the warning when it has one", async () => {
    serve({ score: { ensemble: { ...VALIDATED, r2_score: -0.4 } } });
    const { container } = renderWithQuery(<ForecastComparePage />);
    await waitFor(() => expect(container.textContent).toContain("Negative R²"));
    expect(container.textContent).toContain("ensemble's test set has 6 samples");
  });
});

describe("ForecastComparePage: what the numbers and labels claim", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    stubChartLayout();
  });

  // F063: the header says calendar days with dates, not "3d history".
  it("says how many calendar days the history spans, with both dates", async () => {
    serve({});
    const { container } = renderWithQuery(<ForecastComparePage />);
    await waitFor(() => expect(container.textContent).toContain("calendar days"));
    expect(container.textContent).toContain("3 calendar days, 2026-09-01 → 2026-09-03");
    expect(container.textContent).not.toContain("3d history");
  });

  // F065: the marker is the last history date, labelled with that date.
  it("labels the last-history marker with its date, not as now", async () => {
    serve({});
    const { container } = renderWithQuery(<ForecastComparePage />);
    await waitFor(() => expect(container.textContent).toContain("last observation 2026-09-03"));
    expect(container.textContent).not.toMatch(/\bnow\b/);
  });

  // F068: negative R² means worse than the test-set mean; a small test set is a
  // possible reason, not the established one.
  it("says a negative R² means worse than predicting the mean, not insufficient data", async () => {
    serve({ score: { prophet: { ...VALIDATED, r2_score: -0.4 } } });
    const { container } = renderWithQuery(<ForecastComparePage />);
    await waitFor(() => expect(container.textContent).toContain("Negative R²"));
    expect(container.textContent).toContain("did worse than predicting the mean of its test data");
    expect(container.textContent).not.toContain("insufficient test data");
  });

  // F069: no promise about when the metrics will settle.
  it("makes no promise about when the metrics will stabilise", async () => {
    serve({ score: { prophet: { ...VALIDATED, r2_score: -0.4 } } });
    const { container } = renderWithQuery(<ForecastComparePage />);
    await waitFor(() => expect(container.textContent).toContain("Negative R²"));
    expect(container.textContent).not.toMatch(/stabili[sz]e/);
    expect(container.textContent).not.toContain("30+");
    expect(container.textContent).not.toContain("60 days");
  });
});

describe("ForecastComparePage: the bands are nominal intervals, not confidence intervals", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    stubChartLayout();
  });

  // The Forecast page labels its single band with BAND_LABEL, the words #461
  // established as all the response supports. This page's legend called three
  // bands "Ensemble CI", "Prophet CI" and "LSTM CI", and its toggle said
  // "Show confidence intervals". Neither word is backed by a coverage figure.
  const NOMINAL = "nominal 90% interval (model-based, coverage not validated)";
  const legend = (c: HTMLElement) =>
    Array.from(c.querySelectorAll(".recharts-legend-item-text")).map((el) => el.textContent ?? "");

  it("names each band in the legend with the Forecast page's wording", async () => {
    serve({});
    const { container } = renderWithQuery(<ForecastComparePage />);
    await waitFor(() => expect(legend(container)).toContain(`Ensemble: ${NOMINAL}`));
    expect(legend(container)).toContain(`Prophet: ${NOMINAL}`);
    expect(legend(container)).toContain(`LSTM: ${NOMINAL}`);
  });

  it("does not call any band a CI in the legend", async () => {
    serve({});
    const { container } = renderWithQuery(<ForecastComparePage />);
    await waitFor(() => expect(legend(container).length).toBeGreaterThan(0));
    expect(legend(container).join("\n")).not.toMatch(/\bCI\b/);
    expect(container.textContent).not.toMatch(/confidence interval/i);
  });

  it("words the band toggle as a nominal interval, not a confidence interval", async () => {
    serve({});
    const { container } = renderWithQuery(<ForecastComparePage />);
    await waitFor(() => expect(legend(container).length).toBeGreaterThan(0));
    const toggle = container.querySelector('input[type="checkbox"]')?.closest("label");
    expect(toggle?.textContent).toContain(NOMINAL);
    expect(toggle?.textContent).not.toMatch(/confidence/i);
  });

  // Control: the series that are values, not bands, keep their plain names.
  it("still names the history and the three forecasts in the legend", async () => {
    serve({});
    const { container } = renderWithQuery(<ForecastComparePage />);
    await waitFor(() => expect(legend(container)).toContain("History"));
    expect(legend(container)).toEqual(expect.arrayContaining(["History", "Ensemble", "Prophet", "LSTM"]));
  });
});
