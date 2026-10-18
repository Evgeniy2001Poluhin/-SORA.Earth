import { act, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { LSTMProgressWidget } from "./LSTMProgressWidget";
import { renderWithQuery } from "@/test/utils";
import { stubJson, stubStatus } from "@/test/http";

vi.mock("@/api/mock", async (original) => ({
  ...(await original<typeof import("@/api/mock")>()), isMock: false,
}));

const base = { status: "ok", active: true, samples: 40, threshold: 33,
  observed_samples: 10, eligible_for_training: false, observations_remaining: 23,
  days_remaining: 0, next_activation_date: null, models_active: ["Prophet"],
  weights: { lstm: 0, prophet: 1 }, message: "", reason_code: null };

async function renderAnswer(body: object) {
  stubJson(body);
  const rendered = renderWithQuery(<LSTMProgressWidget />);
  await waitFor(() => expect(rendered.container.textContent).not.toContain("Loading LSTM"));
  return rendered;
}

it.each([false, null, undefined])("does not infer Active from eligibility when membership is %s", async (member) => {
  const { container } = await renderAnswer({ ...base, lstm_in_ensemble: member });
  expect(container.textContent).not.toContain("✓ Active");
  expect(container.textContent).not.toContain("LSTM is active in ensemble");
  expect(container.textContent).toContain(member === false ? "Not in ensemble" : "Unknown");
});

it("shows actual positive membership", async () => {
  const { container } = await renderAnswer({ ...base, observed_samples: 40,
    eligible_for_training: true, observations_remaining: 0, lstm_in_ensemble: true,
    models_active: ["Lstm", "Prophet"], weights: { lstm: .2, prophet: .8 } });
  expect(container.textContent).toContain("✓ Active");
  expect(container.textContent).toContain("LSTM 20%, Prophet 80%");
});

it("counts observed points, not interpolation, towards the training gate", async () => {
  const { container } = await renderAnswer({ ...base, lstm_in_ensemble: false });
  expect(container.textContent).toContain("10 / 33 observations");
  expect(container.textContent).toContain("23 more observations");
  expect(container.textContent).not.toContain("40 / 33");
  expect(container.textContent).not.toContain("accurate");
});

it("does not reinterpret an old dense-row payload as observation progress", async () => {
  const { container } = await renderAnswer({ status: "ok", active: true, samples: 40, threshold: 33 });
  expect(container.textContent).toContain("Observation count unknown");
  expect(container.textContent).not.toContain("✓ Active");
});

it("shows unknown weights after a failed fit, without a healthy banner", async () => {
  const { container } = await renderAnswer({ ...base, lstm_in_ensemble: null,
    eligible_for_training: true, observed_samples: 40, observations_remaining: 0,
    weights: {}, models_active: [], reason_code: "ensemble_fit_failed" });
  expect(container.textContent).toContain("Unknown");
  expect(container.textContent).toContain("fit failed");
  expect(container.textContent).not.toContain("LSTM 0%");
});

it("preserves a measured zero weight", async () => {
  const { container } = await renderAnswer({ ...base, lstm_in_ensemble: false });
  expect(container.textContent).toContain("LSTM 0%, Prophet 100%");
});

it("failed refresh hides a cached Active answer and recovery restores it", async () => {
  const { container, queryClient } = await renderAnswer({ ...base, lstm_in_ensemble: true });
  expect(container.textContent).toContain("✓ Active");
  stubStatus(503);
  await act(async () => { await queryClient.invalidateQueries({ queryKey: ["lstm-status"] }); });
  await waitFor(() => expect(container.textContent).toContain("Unavailable"));
  expect(container.textContent).not.toContain("✓ Active");
  stubJson({ ...base, lstm_in_ensemble: true });
  await act(async () => { await queryClient.invalidateQueries({ queryKey: ["lstm-status"] }); });
  await waitFor(() => expect(container.textContent).toContain("✓ Active"));
});
