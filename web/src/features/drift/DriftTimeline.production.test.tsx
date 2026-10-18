/**
 * The drift timeline and the KS table against what the server measured.
 *
 * Each block here used to draw a number nobody measured: a missing KS
 * statistic as 0.0000, a missing p-value as 1.0000 (which reads as strong
 * evidence of no drift), a 0.31 threshold the server never sent, and a feature
 * with no decision reading as "stable". The fixtures are shaped as the server
 * declares them: `/api/v1/model/drift` (app/api/drift.py, `compute_drift`) and
 * `/api/v1/model/drift/mlflow-history` (app/api/drift.py, `drift_mlflow_history`).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { waitFor } from "@testing-library/react";

vi.mock("@/api/mock", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/mock")>();
  return { ...actual, isMock: false };
});

import { DriftTimeline } from "./DriftTimeline";
import { isMock } from "@/api/mock";
import { renderWithQuery, stubChartLayout } from "@/test/utils";

const urlOf = (input: RequestInfo | URL) =>
  typeof input === "string" ? input : input instanceof URL ? input.href : input.url;

const NO_EVENTS = { status: "ok", events: [], count: 0, reason_code: null };

/** Route by URL: the timeline asks for the MLflow history and the KS report. */
function serve(ks: unknown, mlflow: unknown = NO_EVENTS) {
  vi.spyOn(globalThis, "fetch").mockImplementation((async (input: RequestInfo | URL) => {
    const url = urlOf(input);
    const body = url.includes("/model/drift/mlflow-history")
      ? mlflow
      : url.includes("/model/drift")
        ? ks
        : undefined;
    if (body === undefined) return new Response("not part of this test", { status: 503 });
    return new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
  }) as typeof fetch);
}

/** A KS report with one feature, as `compute_drift` sends it when status is "ok". */
const ksWith = (feature: Record<string, unknown>) => ({
  status: "ok", drift_detected: true, window: 50, observations: 50, reason_code: null,
  features: { budget: feature },
});

/** The KS table row for one feature. Its name cell is the only monospace cell
 *  in the row; the MLflow table's run ids are monospace too, but they are not
 *  feature names. */
const ksRow = (container: HTMLElement, feature: string): HTMLElement => {
  const row = Array.from(container.querySelectorAll<HTMLElement>(".drift-row"))
    .find((r) => r.querySelector(".mono")?.textContent === feature);
  expect(row, `a KS row for ${feature}`).toBeTruthy();
  return row!;
};

/** The MLflow table row whose run id prefix is `run` (the first 8 characters). */
const runRow = (container: HTMLElement, run: string): HTMLElement => {
  const row = Array.from(container.querySelectorAll<HTMLElement>(".drift-row"))
    .find((r) => r.querySelector(".mono")?.textContent === run);
  expect(row, `an MLflow row for run ${run}`).toBeTruthy();
  return row!;
};

const tabularCells = (row: HTMLElement) =>
  Array.from(row.querySelectorAll<HTMLElement>(".tabular"));

describe("the guard on these tests", () => {
  it("really is in production mode", () => {
    expect(isMock).toBe(false);
  });
});

describe("the KS table shows only what the server measured (F058, F059)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the KS statistic as unavailable, not 0.0000, when the server sent none", async () => {
    serve(ksWith({ p_value: 0.2, drift: false }));

    const { container } = renderWithQuery(<DriftTimeline />);
    await waitFor(() => ksRow(container, "budget"), { timeout: 3000 });

    const [statCell] = tabularCells(ksRow(container, "budget"));
    expect(statCell.textContent).not.toBe("0.0000");
    expect(statCell.textContent).toBe("unavailable");
    // Not coloured, and not a value anything could count.
    expect(statCell.style.color).toBe("");
  });

  it("still renders a real KS statistic of zero as 0.0000", async () => {
    serve(ksWith({ ks_stat: 0, p_value: 1, drift: false }));

    const { container } = renderWithQuery(<DriftTimeline />);
    await waitFor(() => ksRow(container, "budget"), { timeout: 3000 });

    expect(tabularCells(ksRow(container, "budget")).map((c) => c.textContent)).toEqual(["0.0000", "1.0000"]);
  });

  it("shows the p-value as unavailable, not 1.0000, when the server sent none", async () => {
    // 1.0000 is the strongest "no drift" a reader can see. A missing p-value
    // must not look like that.
    serve(ksWith({ ks_stat: 0.41, drift: false }));

    const { container } = renderWithQuery(<DriftTimeline />);
    await waitFor(() => ksRow(container, "budget"), { timeout: 3000 });

    const [, pCell] = tabularCells(ksRow(container, "budget"));
    expect(pCell.textContent).not.toBe("1.0000");
    expect(pCell.textContent).toBe("unavailable");
    expect(pCell.style.color).toBe("");
  });

  it("still renders a real p-value of zero, and marks the feature as drift the server flagged", async () => {
    serve(ksWith({ ks_stat: 0.9, p_value: 0, drift: true }));

    const { container } = renderWithQuery(<DriftTimeline />);
    await waitFor(() => ksRow(container, "budget"), { timeout: 3000 });

    const row = ksRow(container, "budget");
    const cells = tabularCells(row);
    expect(cells.map((c) => c.textContent)).toEqual(["0.9000", "0.0000"]);
    expect(row.querySelector(".drift-pill")?.textContent).toBe("DRIFT");
    expect(cells[1].style.color).toBe("rgb(239, 68, 68)");
  });

  it("does not call a feature stable when the server sent no decision", async () => {
    // `drift` is the server's verdict. Its absence used to fall through to the
    // "stable" pill, a green claim the server never made.
    serve(ksWith({ ks_stat: 0.4, p_value: 0.3 }));

    const { container } = renderWithQuery(<DriftTimeline />);
    await waitFor(() => ksRow(container, "budget"), { timeout: 3000 });

    const pill = ksRow(container, "budget").querySelector(".drift-pill");
    expect(pill?.textContent).not.toBe("stable");
    expect(pill?.textContent).toBe("unknown");
  });
});

describe("the KS footnote states the server's rule (F059, same block)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("does not state p < 0.01 as the rejection rule", async () => {
    // app/api/drift.py sets SIGNIFICANCE = 0.05 and marks a feature with
    // `p < SIGNIFICANCE`. The footnote used to say 0.01, and the p-value cell
    // was coloured on that cut-off too.
    serve(ksWith({ ks_stat: 0.4, p_value: 0.03, drift: true }));

    const { container } = renderWithQuery(<DriftTimeline />);
    await waitFor(() => ksRow(container, "budget"), { timeout: 3000 });

    expect(container.textContent ?? "").not.toMatch(/p\s*<\s*0\.01/);
  });
});

describe("the timeline is not judged against a threshold the server never sent (F057)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    stubChartLayout();
  });

  const ONE_EVENT = {
    status: "ok", reason_code: null, count: 1, events: [{
      run_id: "0123456789abcdef", start_time: "2026-09-05T10:00:00Z",
      "metrics.drift_score": 0.5, "metrics.drifted_features_count": 2,
      "tags.baseline_id": "baseline-x", "params.drifted_features": "budget,co2",
    }],
  };

  it("draws no 0.31 reference line", async () => {
    // The backend returns no threshold, and the drift timeline's trigger is
    // not 0.31 (app/mlflow_tracking.py writes a run whenever one feature was
    // flagged). Asserted on the chart's own label, which is drawn only when the
    // chart renders.
    serve(ksWith({ ks_stat: 0.4, p_value: 0.3, drift: false }), ONE_EVENT);

    const { container } = renderWithQuery(<DriftTimeline />);
    await waitFor(() => runRow(container, "01234567"), { timeout: 3000 });

    expect(container.textContent ?? "").not.toContain("threshold 0.31");
  });

  it("does not colour a score red because it passes 0.31", async () => {
    serve(ksWith({ ks_stat: 0.4, p_value: 0.3, drift: false }), ONE_EVENT);

    const { container } = renderWithQuery(<DriftTimeline />);
    await waitFor(() => runRow(container, "01234567"), { timeout: 3000 });

    const [scoreCell] = tabularCells(runRow(container, "01234567"));
    expect(scoreCell.textContent).toBe("50%");
    expect(scoreCell.style.color).not.toBe("rgb(239, 68, 68)");
  });

  it("still shows a recorded score of zero as 0%", async () => {
    serve(ksWith({ ks_stat: 0.4, p_value: 0.3, drift: false }), {
      ...ONE_EVENT,
      events: [{ ...ONE_EVENT.events[0], "metrics.drift_score": 0 }],
    });

    const { container } = renderWithQuery(<DriftTimeline />);
    await waitFor(() => runRow(container, "01234567"), { timeout: 3000 });

    expect(tabularCells(runRow(container, "01234567"))[0].textContent).toBe("0%");
  });
});
