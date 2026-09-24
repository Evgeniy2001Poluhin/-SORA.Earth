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
import { renderWithQuery } from "@/test/utils";
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
