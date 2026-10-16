/**
 * The LSTM progress widget against what `/api/v1/lstm-status` measured.
 *
 * Three claims on this widget went past the server: a countdown stated as a
 * ready date, weights of 0% that the server never sent, and a footer that
 * promised "accurate" forecasts for a row-count gate. The fixtures follow
 * `LstmStatusOk` (app/schemas.py) and `get_lstm_status` (app/api/forecast.py).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { waitFor } from "@testing-library/react";

vi.mock("@/api/mock", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/mock")>();
  return { ...actual, isMock: false };
});

import { LSTMProgressWidget } from "./LSTMProgressWidget";
import { isMock } from "@/api/mock";
import { renderWithQuery } from "@/test/utils";
import { stubJson } from "@/test/http";

/** Not yet active: 13 more rows are needed at the server's threshold of 33. */
const COLLECTING = {
  status: "ok", active: false, samples: 20, threshold: 33, days_remaining: 13,
  next_activation_date: "2026-11-01", models_active: ["Prophet", "LinearTrend"],
  weights: { lstm: 0.0, prophet: 0.9, linear: 0.1 }, unique_days_raw: 20,
  last_evaluation_date: "2026-10-16", message: "Need 13 more days of data", reason_code: null,
};

const ACTIVE = {
  ...COLLECTING, active: true, samples: 40, days_remaining: 0, next_activation_date: null,
  models_active: ["Lstm", "Prophet"], weights: { lstm: 0.2, prophet: 0.8, linear: 0.0 },
  message: "LSTM active",
};

describe("the guard on these tests", () => {
  it("really is in production mode", () => {
    expect(isMock).toBe(false);
  });
});

describe("the countdown is a conditional estimate, not a date (F060)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("states the days as an estimate at one sample a day, and gives no ready date", async () => {
    // `/api/v1/lstm-status` assumes one evaluation a day (forecast.py, "assuming
    // 1 eval/day growth rate"). A countdown read as "ready in 13 days" with a
    // date beside it reads as a forecast of when the model will be ready.
    stubJson(COLLECTING);

    const { container } = renderWithQuery(<LSTMProgressWidget />);
    await waitFor(() => expect(container.textContent ?? "").toContain("20 / 33"), { timeout: 3000 });

    const text = container.textContent ?? "";
    expect(text).toContain("at about one sample a day");
    expect(text).toContain("about 13 days");
    expect(text).not.toContain("2026-11-01");
    expect(text).not.toContain("ready in");
  });
});

describe("the ensemble weights are shown as measured, or as unknown (F061)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("shows unknown rather than 0% when the server sent no weights", async () => {
    stubJson({ ...ACTIVE, weights: {} });

    const { container } = renderWithQuery(<LSTMProgressWidget />);
    await waitFor(() => expect(container.textContent ?? "").toContain("Weights:"), { timeout: 3000 });

    const text = container.textContent ?? "";
    expect(text).toContain("LSTM unknown, Prophet unknown");
    expect(text).not.toContain("LSTM 0%");
    expect(text).not.toContain("Prophet 0%");
  });

  it("still renders a real weight of zero as 0%", async () => {
    // LSTM skipped or dropped by the ensemble: the weight really is zero.
    stubJson({ ...ACTIVE, weights: { lstm: 0, prophet: 1, linear: 0 } });

    const { container } = renderWithQuery(<LSTMProgressWidget />);
    await waitFor(() => expect(container.textContent ?? "").toContain("Weights:"), { timeout: 3000 });

    expect(container.textContent ?? "").toContain("LSTM 0%, Prophet 100%");
  });

  it("shows the weights the server reports", async () => {
    stubJson({ ...ACTIVE, weights: { lstm: 0.7, prophet: 0.3, linear: 0.0 } });

    const { container } = renderWithQuery(<LSTMProgressWidget />);
    await waitFor(() => expect(container.textContent ?? "").toContain("Weights:"), { timeout: 3000 });

    expect(container.textContent ?? "").toContain("LSTM 70%, Prophet 30%");
  });
});

describe("the threshold footer says what the gate does (F062)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("says the samples are needed before the LSTM is trained, and claims no accuracy", async () => {
    // app/services/forecasting/ensemble.py skips the LSTM below LSTM_MIN_ROWS
    // (33). Reaching it is eligibility to be trained, not an accuracy result.
    stubJson(COLLECTING);

    const { container } = renderWithQuery(<LSTMProgressWidget />);
    await waitFor(() => expect(container.textContent ?? "").toContain("20 / 33"), { timeout: 3000 });

    const text = container.textContent ?? "";
    expect(text).toContain("33 samples needed before the LSTM is trained");
    expect(text).not.toContain("accurate");
  });
});
