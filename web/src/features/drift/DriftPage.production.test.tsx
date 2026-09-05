/**
 * The drift page against a successful-but-empty answer (#236).
 *
 * #236 was filed about `UncertaintyCard` and closed with a request: check the
 * neighbouring cards for the same `?? 0`-over-`if (!data)` shape. This file is
 * the answer for the loudest one found.
 *
 * `{}` is truthy, so `if (!d)` passed it through, `d.drift_detected` was
 * undefined and therefore falsy, and the status KPI rendered a green
 * **STABLE** next to a drift score of "NaN%". On the model-monitoring page,
 * that is a verdict nobody computed telling an operator the model is fine —
 * the #216 class at its worst, since this is the page you look at precisely
 * when you want to know whether to trust the model.
 *
 * Measured with a transport stub returning 200 and `{}`, not deduced.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";

vi.mock("@/api/mock", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/mock")>();
  return { ...actual, isMock: false };
});

import { DriftPage } from "./DriftPage";
import { LSTMProgressWidget } from "./LSTMProgressWidget";
import { DriftTimeline } from "./DriftTimeline";
import { isMock } from "@/api/mock";
import { renderWithQuery } from "@/test/utils";
import { stubJson, stubStatus } from "@/test/http";

/** A real `check_drift()` answer, with values no canned payload produces. */
const SERVER_DRIFT = {
  status: "drift_detected",
  timestamp: "2026-09-05T00:00:00Z",
  observations: 417,
  drift_detected: true,
  drift_score: 0.6125,
  drifted_features: ["budget", "co2_reduction"],
  features: {},
  recent_alerts: [],
};

describe("the guard on these tests", () => {
  it("really is in production mode", () => {
    expect(isMock).toBe(false);
  });
});

describe("DriftPage when the API answers with nothing", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("does not report the model STABLE on an empty 200", async () => {
    stubJson({});

    const { container } = renderWithQuery(<DriftPage />);

    await waitFor(
      () => expect(screen.getByText(/No drift data available yet/i)).toBeInTheDocument(),
      { timeout: 3000 },
    );
    const text = container.textContent ?? "";
    expect(text).not.toContain("STABLE");
    expect(text).not.toContain("DRIFT DETECTED");
    // The NaN came from `d.drift_score * 100` on an absent score.
    expect(text).not.toContain("NaN");
  });

  it("shows the same empty state when the request fails", async () => {
    stubStatus(500);

    renderWithQuery(<DriftPage />);

    await waitFor(() => expect(screen.getByText(/Failed to load drift status/i)).toBeInTheDocument(), {
      timeout: 3000,
    });
  });

  it("still renders a real verdict the server did compute", async () => {
    // The other half: a guard that hides real answers is worse than the bug.
    stubJson(SERVER_DRIFT);

    const { container } = renderWithQuery(<DriftPage />);

    await waitFor(() => expect(screen.getByText("DRIFT DETECTED")).toBeInTheDocument(), {
      timeout: 3000,
    });
    expect(container.textContent ?? "").toContain("61%");
  });

  it("renders a no_baseline answer rather than swallowing it", async () => {
    // `check_drift()` returns status/drift_detected/drift_score on every
    // branch, so "no baseline yet" is a real answer and must reach the screen.
    stubJson({ ...SERVER_DRIFT, status: "no_baseline", drift_detected: false, drift_score: 0.0, observations: 3 });

    renderWithQuery(<DriftPage />);

    await waitFor(() => expect(screen.getByText("NO BASELINE")).toBeInTheDocument(), {
      timeout: 3000,
    });
    expect(screen.queryByText(/No drift data available yet/i)).not.toBeInTheDocument();
  });
});

describe("LSTMProgressWidget when the API answers with nothing", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("draws no progress bar instead of one reading NaN%", async () => {
    stubJson({});

    const { container } = renderWithQuery(<LSTMProgressWidget />);
    await waitFor(() => expect(container.querySelector(".lstm-progress-widget.loading")).toBeNull(), {
      timeout: 3000,
    });

    expect(container.textContent ?? "").not.toContain("NaN");
    expect(container.querySelector(".progress-bar-fill")).toBeNull();
  });

  it("draws nothing rather than dividing by a zero threshold", async () => {
    // `samples / 0` is Infinity, which `Math.min(_, 100)` quietly renders as a
    // full bar — a finished-looking progress bar for a threshold of nothing.
    stubJson({ active: false, samples: 40, threshold: 0, days_remaining: 0, next_activation_date: null, models_active: [], weights: {}, message: "" });

    const { container } = renderWithQuery(<LSTMProgressWidget />);
    await waitFor(() => expect(container.querySelector(".lstm-progress-widget.loading")).toBeNull(), {
      timeout: 3000,
    });

    expect(container.querySelector(".progress-bar-fill")).toBeNull();
  });

  it("renders the real progress the server reports", async () => {
    stubJson({ active: false, samples: 137, threshold: 500, days_remaining: 12, next_activation_date: null, models_active: [], weights: {}, message: "" });

    const { container } = renderWithQuery(<LSTMProgressWidget />);

    await waitFor(() => expect(container.textContent ?? "").toContain("137 / 500"), {
      timeout: 3000,
    });
    expect(container.textContent ?? "").toContain("27.4%");
  });
});

describe("DriftTimeline when an event arrives malformed", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("guards the payload correctly already", async () => {
    // Included as the negative control for the sweep: this component was
    // checked and its top-level guard is the shape the others now use.
    stubJson({});

    const { container } = renderWithQuery(<DriftTimeline />);
    await waitFor(() => expect(container.textContent ?? "").toContain("No drift events yet"), {
      timeout: 3000,
    });
    expect(container.textContent ?? "").toContain("0 events");
  });

  it("survives an event with no run_id instead of taking the page down", async () => {
    // One level below the payload guard: every field in the row had a
    // fallback except `run_id`, and `.slice` on undefined unmounted the
    // whole drift page, since the timeline renders inside it.
    stubJson({ events: [{ start_time: "2026-09-05T10:00:00Z", "metrics.drift_score": 0.42 }] });

    const { container } = renderWithQuery(<DriftTimeline />);
    await waitFor(() => expect(container.textContent ?? "").toContain("1 events"), { timeout: 3000 });

    expect(container.textContent ?? "").not.toBe("");
    expect(container.textContent ?? "").toContain("42%");
  });

  it("shows a dash rather than an Invalid Date when the timestamp is absent", async () => {
    // Quieter than the crash and worse: an absent start_time sorts as NaN,
    // which reorders the timeline rather than failing.
    stubJson({ events: [{ run_id: "abcdef1234567890", "metrics.drift_score": 0.5 }] });

    const { container } = renderWithQuery(<DriftTimeline />);
    await waitFor(() => expect(container.textContent ?? "").toContain("abcdef12"), { timeout: 3000 });

    expect(container.textContent ?? "").not.toContain("Invalid Date");
  });

  it("still renders a well-formed event exactly as before", async () => {
    stubJson({
      events: [{
        run_id: "0123456789abcdef",
        start_time: "2026-09-05T10:00:00Z",
        "metrics.drift_score": 0.73,
        "metrics.drifted_features_count": 3,
        "tags.baseline_id": "baseline-x",
        "params.drifted_features": "budget,co2",
      }],
    });

    const { container } = renderWithQuery(<DriftTimeline />);
    await waitFor(() => expect(container.textContent ?? "").toContain("01234567"), { timeout: 3000 });

    const text = container.textContent ?? "";
    expect(text).toContain("73%");
    expect(text).toContain("baseline-x");
  });
});
