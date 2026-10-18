/**
 * The forecast page when no forecast was made.
 *
 * `/forecast` has two early returns -- no history at all, or fewer than three
 * points -- and both build `ForecastResponse` without a `confidence`, which the
 * schema declares `Optional[...] = None` (app/api/forecast.py, app/schemas.py).
 * So the server says, in so many words, that there is no confidence because
 * there is no forecast. The page read `data?.confidence ?? "low"` and printed
 * **LOW** confidence for a forecast that does not exist: a grade nobody gave.
 *
 * Measured with a transport stub returning the early-return body.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("@/api/mock", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/mock")>();
  return { ...actual, isMock: false };
});

import ForecastPage from "./ForecastPage";
import { isMock } from "@/api/mock";
import { renderWithQuery, stubChartLayout } from "@/test/utils";
import { stubJson } from "@/test/http";

const renderPage = () => renderWithQuery(<MemoryRouter><ForecastPage /></MemoryRouter>);

/** The value above the "confidence" caption. The block renders only once the
 *  answer is in, so its presence is also the signal that the query settled. */
const confidence = (c: HTMLElement): string | null => {
  const stat = Array.from(c.querySelectorAll(".stat")).find((s) => s.querySelector("em")?.textContent === "confidence");
  return stat?.querySelector("b")?.textContent ?? null;
};

describe("the guard on these tests", () => {
  it("really is in production mode", () => {
    expect(isMock).toBe(false);
  });
});

describe("ForecastPage confidence", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("does not grade a forecast that was never made", async () => {
    // The body of the first early return, as FastAPI serialises it.
    stubJson({ history: [], forecast: [], model: "linear-trend", metric: "score", confidence: null, metadata: null });
    const { container } = renderPage();
    await waitFor(() => expect(confidence(container)).not.toBeNull());
    expect(confidence(container)).not.toBe("LOW");
    expect(confidence(container)).toBe("—");
  });

  it("shows the confidence the server gave", async () => {
    // Control: an unconditional dash would pass the test above.
    stubJson({
      history: [1, 2, 3, 4, 5].map((i) => ({ ds: `2026-09-0${i}`, y: 60 + i })),
      forecast: [6, 7, 8].map((i) => ({ ds: `2026-09-0${i}`, yhat: 66 + i, yhat_lower: 60 + i, yhat_upper: 72 + i })),
      model: "ensemble", metric: "score", confidence: "medium", metadata: null,
    });
    const { container } = renderPage();
    await waitFor(() => expect(confidence(container)).toBe("MEDIUM"));
  });
});

/** Calendar days 2026-09-DD, as the server writes `ds`. */
const day = (i: number) => `2026-09-${String(i).padStart(2, "0")}`;
const historyOf = (days: number[]) => days.map((i) => ({ ds: day(i), y: 60 + i }));
const forecastOf = (days: number[]) =>
  days.map((i) => ({ ds: day(i), yhat: 66 + i, yhat_lower: 60 + i, yhat_upper: 72 + i }));

describe("ForecastPage: what the numbers and labels claim", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    stubChartLayout();
  });

  // F063: a row is a calendar day (the server fills gaps), so say days, with dates.
  it("says how many calendar days the history spans, with both dates", async () => {
    stubJson({
      history: historyOf([1, 2, 3, 4, 5]), forecast: forecastOf([6, 7, 8]),
      model: "ensemble", metric: "score", confidence: "medium", metadata: null,
    });
    const { container } = renderPage();
    await waitFor(() => expect(confidence(container)).toBe("MEDIUM"));
    expect(container.textContent).toContain("5 calendar days, 2026-09-01 → 2026-09-05");
    expect(container.textContent).not.toContain("5d history");
  });

  // F064: the marker is the last history date, so it is not "now".
  it("labels the last-history marker with its date, not as now", async () => {
    stubJson({
      history: historyOf([1, 2, 3, 4, 5]), forecast: forecastOf([6, 7, 8]),
      model: "ensemble", metric: "score", confidence: "medium", metadata: null,
    });
    const { container } = renderPage();
    await waitFor(() => expect(container.textContent).toContain("last observation 2026-09-05"));
    expect(container.textContent).not.toMatch(/\bnow\b/);
  });

  // F066: the response has no coverage, level or method field, so the band may
  // only be called a nominal interval.
  it("names the band a nominal interval whose coverage is not validated", async () => {
    stubJson({
      history: historyOf([1, 2, 3, 4, 5]), forecast: forecastOf([6, 7, 8]),
      model: "ensemble", metric: "score", confidence: "medium", metadata: null,
    });
    const { container } = renderPage();
    await waitFor(() =>
      expect(container.textContent).toContain("nominal 90% interval (model-based, coverage not validated)"),
    );
    expect(container.textContent).not.toContain("90% CI");
  });

  // F067: the backend's avg_ci_width is upper minus lower, the full width.
  it("shows a width of 10 as a width, never as ±10", async () => {
    stubJson({
      history: historyOf([1, 2, 3, 4, 5]), forecast: forecastOf([6, 7, 8]),
      model: "lstm", metric: "score", confidence: "medium",
      metadata: { mc_samples: 50, avg_ci_width: 10, max_ci_width: 12, seq_length: 14, autoregressive: true },
    });
    const { container } = renderPage();
    await waitFor(() => expect(container.textContent).toContain("Avg interval width"));
    expect(container.textContent).toContain("10.000");
    expect(container.textContent).not.toContain("±");
  });
});
