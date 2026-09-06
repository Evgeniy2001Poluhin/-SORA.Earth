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
import userEvent from "@testing-library/user-event";

vi.mock("@/api/mock", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/mock")>();
  return { ...actual, isMock: false };
});

// Toasts render inside sonner's <Toaster>, which this page does not mount, so
// there is nothing in the DOM to assert on. The calls are the observable.
const toastCalls: Array<{ kind: string; text: string }> = [];
vi.mock("sonner", () => {
  const record = (kind: string) => (text: string) => {
    toastCalls.push({ kind, text: String(text) });
  };
  const toast = Object.assign(record("plain"), {
    success: record("success"),
    error: record("error"),
  });
  return { toast, Toaster: () => null };
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

  it("says the status is unavailable rather than vanishing", async () => {
    // The failure branch is a 503 now, so the query rejects and `data` is
    // absent -- which is the same state as "nothing loaded yet", and the widget
    // used to render nothing at all. A fault that leaves no trace on the page
    // is the quiet kind of wrong this whole effort is about.
    stubStatus(503);

    const { container } = renderWithQuery(<LSTMProgressWidget />);

    await waitFor(
      () => expect(container.textContent ?? "").toContain("could not be determined"),
      { timeout: 3000 },
    );
    expect(container.textContent ?? "").not.toContain("samples");
  });

  it("draws the active block even if the server omits its lists", async () => {
    // The contract promises `models_active` and `weights` when active is true.
    // Reading them on the strength of `active` alone is the mistake #236 was
    // about, one level in.
    stubJson({
      status: "ok", active: true, samples: 40, threshold: 33, days_remaining: 0,
      next_activation_date: null, message: "LSTM active", reason_code: null,
      unique_days_raw: 40, last_evaluation_date: "2026-09-01",
    });

    const { container } = renderWithQuery(<LSTMProgressWidget />);

    await waitFor(() => expect(container.textContent ?? "").toContain("LSTM is active"), {
      timeout: 3000,
    });
    expect(container.textContent ?? "").not.toContain("NaN");
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

// The envelope carries `status` since the #241 follow-up migrated this
// endpoint: an unreachable MLflow answers 503 rather than an empty list, so
// these fixtures declare the successful status explicitly instead of relying
// on a bare `events` key.
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
    stubJson({ status: "ok", reason_code: null, count: 1, events: [{ start_time: "2026-09-05T10:00:00Z", "metrics.drift_score": 0.42 }] });

    const { container } = renderWithQuery(<DriftTimeline />);
    await waitFor(() => expect(container.textContent ?? "").toContain("1 events"), { timeout: 3000 });

    expect(container.textContent ?? "").not.toBe("");
    expect(container.textContent ?? "").toContain("42%");
  });

  it("shows a dash rather than an Invalid Date when the timestamp is absent", async () => {
    // Quieter than the crash and worse: an absent start_time sorts as NaN,
    // which reorders the timeline rather than failing.
    stubJson({ status: "ok", reason_code: null, count: 1, events: [{ run_id: "abcdef1234567890", "metrics.drift_score": 0.5 }] });

    const { container } = renderWithQuery(<DriftTimeline />);
    await waitFor(() => expect(container.textContent ?? "").toContain("abcdef12"), { timeout: 3000 });

    expect(container.textContent ?? "").not.toContain("Invalid Date");
  });

  it("still renders a well-formed event exactly as before", async () => {
    stubJson({
      status: "ok",
      reason_code: null,
      count: 1,
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


describe("the simulate button against its migrated contract", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  /** Route each request by URL.
   *
   *  A single stub cannot serve this page: the drift query's guard rejects a
   *  body with no `drift_score`, so feeding it the simulate response leaves
   *  the page in its empty state with no buttons at all. The baseline has to
   *  say `exists: true` as well, or the simulate buttons stay disabled.
   */
  const routed = (simulateBody: unknown) => {
    vi.spyOn(globalThis, "fetch").mockImplementation((async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const body = url.includes("/mlops/drift/simulate")
        ? simulateBody
        : url.includes("/mlops/drift/baseline")
          ? { exists: true, n_samples: 500, feature_count: 4 }
          : url.includes("/mlops/drift")
            ? {
                status: "stable", timestamp: "2026-09-06T00:00:00Z", observations: 50,
                drift_detected: false, drift_score: 0.1, drifted_features: [],
                features: {}, recent_alerts: [],
              }
            : { events: [], count: 0, status: "ok", reason_code: null };
      return { ok: true, status: 200, statusText: "OK", json: async () => body } as Response;
    }) as typeof fetch);
  };

  beforeEach(() => {
    toastCalls.length = 0;
  });

  /** The same routing, but the simulate call fails with a body. */
  const routedFailure = (status: number, body: string) => {
    vi.spyOn(globalThis, "fetch").mockImplementation((async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      if (url.includes("/mlops/drift/simulate")) {
        return new Response(body, { status, statusText: "Bad Request" });
      }
      const ok = url.includes("/mlops/drift/baseline")
        ? { exists: true, n_samples: 500, feature_count: 4 }
        : url.includes("/mlops/drift")
          ? {
              status: "stable", timestamp: "2026-09-06T00:00:00Z", observations: 50,
              drift_detected: false, drift_score: 0.1, drifted_features: [],
              features: {}, recent_alerts: [],
            }
          : { events: [], count: 0, status: "ok", reason_code: null };
      return { ok: true, status: 200, statusText: "OK", json: async () => ok } as Response;
    }) as typeof fetch);
  };

  const clickSimulate = async () => {
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Simulate stable/i }));
  };

  it("does not claim a simulation ran when the server skipped it", async () => {
    // The endpoint debounces itself for two seconds and answers `skipped`.
    // The success handler used to announce the mode it had *asked for*,
    // ignoring the response entirely -- so the user was told a simulation had
    // run while the observation window had not been touched.
    routed({
      status: "skipped", mode: null, shift_sigma: null, shifts: {},
      observations: 42, reason_code: "debounced",
    });

    renderWithQuery(<DriftPage />);
    await clickSimulate();

    await waitFor(() => expect(toastCalls.some((t) => /Not simulated/i.test(t.text))).toBe(true), {
      timeout: 3000,
    });
    expect(toastCalls.some((t) => /^Simulated /i.test(t.text))).toBe(false);
  });

  it("still confirms a simulation that did run", async () => {
    routed({
      status: "simulated", mode: "stable", shift_sigma: 0.0,
      shifts: { budget: 0.0 }, observations: 80, reason_code: null,
    });

    renderWithQuery(<DriftPage />);
    await clickSimulate();

    await waitFor(() => expect(toastCalls.some((t) => /Simulated stable/i.test(t.text))).toBe(true), {
      timeout: 3000,
    });
    expect(toastCalls.some((t) => /Not simulated/i.test(t.text))).toBe(false);
  });

  /** The 400 branch, selected structurally rather than by prose (#250).
   *
   *  This handler used to ask `msg.includes("fit baseline first")`. Every case
   *  below was green under that code except the reworded one -- which is the
   *  whole point: the old test could not tell a working hint from one that
   *  worked only because nobody had touched the sentence yet.
   */
  it("offers the baseline hint when the server says which refusal it is", async () => {
    routedFailure(400, JSON.stringify({
      status: "not_fitted", mode: null, shift_sigma: null, shifts: {},
      observations: 0, reason_code: "baseline_not_fitted", detail: "fit baseline first",
    }));

    renderWithQuery(<DriftPage />);
    await clickSimulate();

    await waitFor(() => expect(toastCalls.some((t) => /Fit baseline before simulating/i.test(t.text))).toBe(true), {
      timeout: 3000,
    });
    expect(toastCalls.some((t) => /Simulate failed/i.test(t.text))).toBe(false);
  });

  it("still offers it when the sentence is reworded, because it no longer reads the sentence", async () => {
    // The exact failure the substring match could not survive. Under the old
    // handler this produced "Simulate failed: ...", and the user lost the one
    // instruction that would have unblocked them.
    routedFailure(400, JSON.stringify({
      status: "not_fitted", mode: null, shift_sigma: null, shifts: {},
      observations: 0, reason_code: "baseline_not_fitted",
      detail: "Базовая линия не обучена.",
    }));

    renderWithQuery(<DriftPage />);
    await clickSimulate();

    await waitFor(() => expect(toastCalls.some((t) => /Fit baseline before simulating/i.test(t.text))).toBe(true), {
      timeout: 3000,
    });
  });

  it("falls back to the old phrase while a stale backend is still answering", async () => {
    // The transitional case: a freshly loaded bundle against a backend that
    // has not been redeployed yet, so there is no reason_code to read. This is
    // the only thing keeping the substring in the code, and it is why removing
    // it later is a one-line change with a test that says whether it matters.
    routedFailure(400, JSON.stringify({ detail: "fit baseline first" }));

    renderWithQuery(<DriftPage />);
    await clickSimulate();

    await waitFor(() => expect(toastCalls.some((t) => /Fit baseline before simulating/i.test(t.text))).toBe(true), {
      timeout: 3000,
    });
  });

  it("does not offer the baseline hint for a different refusal", async () => {
    // A reason_code that is present and is not this one must take the generic
    // branch. Without this the fallback could quietly widen to "any 400".
    routedFailure(400, JSON.stringify({
      status: "not_fitted", reason_code: "observation_store_unavailable",
      detail: "fit baseline first",
    }));

    renderWithQuery(<DriftPage />);
    await clickSimulate();

    await waitFor(() => expect(toastCalls.some((t) => /Simulate failed/i.test(t.text))).toBe(true), {
      timeout: 3000,
    });
    expect(toastCalls.some((t) => /Fit baseline before simulating/i.test(t.text))).toBe(false);
  });

  it("reports an unrelated failure as a failure", async () => {
    routedFailure(500, "internal error");

    renderWithQuery(<DriftPage />);
    await clickSimulate();

    await waitFor(() => expect(toastCalls.some((t) => /Simulate failed/i.test(t.text))).toBe(true), {
      timeout: 3000,
    });
    expect(toastCalls.some((t) => /Fit baseline before simulating/i.test(t.text))).toBe(false);
  });
});

describe("the KS table against the migrated contract (#239)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  const ks = (body: unknown) => {
    // DriftTimeline issues two requests; both get the same stub, and only the
    // KS label is asserted here.
    stubJson(body);
  };

  it("says there is no prediction log rather than reporting zero features", async () => {
    // The defect this migration exists for. `{status:"no_log"}` used to reach
    // the screen as "0 features", which reads as a measurement: the KS test
    // ran and found nothing to report. It never ran.
    ks({ status: "no_log", drift_detected: null, window: 50, observations: 0, features: {}, reason_code: "prediction_log_absent" });

    const { container } = renderWithQuery(<DriftTimeline />);

    await waitFor(
      () => expect(container.textContent ?? "").toContain("no prediction log yet"),
      { timeout: 3000 },
    );
    expect(container.textContent ?? "").not.toContain("0 features");
  });

  it("says how short the window is rather than reporting zero features", async () => {
    ks({ status: "insufficient_data", drift_detected: null, window: 50, observations: 4, features: {}, reason_code: "below_minimum_window" });

    const { container } = renderWithQuery(<DriftTimeline />);

    await waitFor(() => expect(container.textContent ?? "").toContain("4 of 10 rows"), {
      timeout: 3000,
    });
    expect(container.textContent ?? "").not.toContain("0 features");
  });

  it("reports an unavailable service as unavailable, not as absence of drift", async () => {
    // A 503 rejects in the client. What must not happen is the KS panel
    // rendering an empty, calm table that reads as "nothing wrong".
    stubStatus(503);

    const { container } = renderWithQuery(<DriftTimeline />);

    await waitFor(
      () => expect(container.textContent ?? "").toContain("could not be run"),
      { timeout: 3000 },
    );
    expect(container.textContent ?? "").not.toContain("0 features");
  });

  it("draws no feature rows for a status that measured nothing, even if some arrive", async () => {
    // The rule: a non-empty `features` means "measured" only when status is
    // "ok". The server's model permits the field on every status, so the
    // client must not render it on the strength of its presence alone.
    //
    // This case needs the fixture to carry features on a not-measured status:
    // with the server's usual empty map, guarding on status and not guarding
    // produce the same empty table, and the assertion cannot tell them apart.
    ks({
      status: "insufficient_data", drift_detected: null, window: 50, observations: 4,
      reason_code: "below_minimum_window",
      features: { budget: { ks_stat: 0.9125, p_value: 0.0001, drift: true } },
    });

    const { container } = renderWithQuery(<DriftTimeline />);

    await waitFor(() => expect(container.textContent ?? "").toContain("4 of 10 rows"), {
      timeout: 3000,
    });
    expect(container.textContent ?? "").not.toContain("0.9125");
  });

  it("still counts the features when the server did measure them", async () => {
    // The other half: a guard that hides a real answer is the worse bug.
    ks({
      status: "ok", drift_detected: true, window: 50, observations: 50, reason_code: null,
      features: {
        budget: { ks_stat: 0.6125, p_value: 0.0001, drift: true },
        social_impact: { ks_stat: 0.1075, p_value: 0.4025, drift: false },
      },
    });

    const { container } = renderWithQuery(<DriftTimeline />);

    await waitFor(() => expect(container.textContent ?? "").toContain("2 features"), {
      timeout: 3000,
    });
    expect(container.textContent ?? "").toContain("0.6125");
  });
});

describe("the MLflow timeline against its migrated contract", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("says MLflow could not be queried rather than showing zero events", async () => {
    // The defect: an unreachable tracking server answered 200 with an empty
    // list, so the screen said "0 events" -- the same thing it says when
    // MLflow is fine and holds nothing. One is a fault, the other is a fact.
    stubStatus(503);

    const { container } = renderWithQuery(<DriftTimeline />);

    // Asserted on the label itself, not on the page text. The first version
    // checked `container.textContent`, which is satisfied by the empty-state
    // line further down -- so removing the label's error branch left the test
    // green while the label still read "0 events". Mutation testing caught it.
    const label = () =>
      Array.from(container.querySelectorAll(".eyebrow")).find((n) =>
        (n.textContent ?? "").startsWith("Drift timeline (MLflow):"),
      );

    await waitFor(() => expect(label()?.textContent ?? "").toContain("could not be queried"), {
      timeout: 3000,
    });
    expect(label()?.textContent ?? "").not.toContain("0 events");
    expect(container.textContent ?? "").not.toContain("No drift events yet");
  });

  it("still says there are no events when MLflow answered and holds none", async () => {
    // The other half: an empty list from a working MLflow is a real answer and
    // must keep reading as one.
    stubJson({ status: "ok", events: [], count: 0, reason_code: null });

    const { container } = renderWithQuery(<DriftTimeline />);

    await waitFor(() => expect(container.textContent ?? "").toContain("0 events"), {
      timeout: 3000,
    });
    expect(container.textContent ?? "").toContain("No drift events yet");
    expect(container.textContent ?? "").not.toContain("could not be queried");
  });

  it("draws no events for a status that reported none, even if some arrive", async () => {
    // `events` is read on the strength of the status plus the array, not the
    // status alone. Not hypothetical: the KS report on the same page also
    // answers `status: "ok"`, carrying no `events` at all -- which is exactly
    // how the first version of this guard crashed.
    stubJson({ status: "unavailable", events: [{ run_id: "should-not-render" }], count: 1, reason_code: "mlflow_unavailable" });

    const { container } = renderWithQuery(<DriftTimeline />);
    await new Promise((resolve) => setTimeout(resolve, 400));

    expect(container.textContent ?? "").not.toContain("should-n");
  });
});
