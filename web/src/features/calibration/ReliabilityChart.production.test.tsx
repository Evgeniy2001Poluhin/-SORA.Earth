/**
 * The reliability diagram against a successful-but-empty answer (#236).
 *
 * Found by widening the sweep: the first pass grepped for `?? 0`, which is a
 * self-selected marker — a component can crash without one. Searching instead
 * for every truthiness guard on a payload turned up this one, and rendering it
 * against `{}` crashed on `curve.bin_lower.map`.
 */
import { describe, expect, it, vi } from "vitest";

vi.mock("@/api/mock", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/mock")>();
  return { ...actual, isMock: false };
});

import { ReliabilityChart } from "./ReliabilityChart";
import { isMock } from "@/api/mock";
import { renderWithQuery, stubChartLayout } from "@/test/utils";

const CURVE = {
  curve: {
    bin_lower: [0, 0.5],
    mean_predicted: [0.25, 0.75],
    mean_observed: [0.3, 0.7],
    count: [12, 8],
  },
};

describe("the guard on these tests", () => {
  it("really is in production mode", () => {
    expect(isMock).toBe(false);
  });
});

describe("ReliabilityChart", () => {
  it("shows the empty state on a payload with no curve", () => {
    stubChartLayout();

    const { container } = renderWithQuery(<ReliabilityChart data={{} as never} />);

    expect(container.querySelector(".rel-empty")).not.toBeNull();
  });

  it("shows the empty state when data is genuinely absent", () => {
    stubChartLayout();

    const { container } = renderWithQuery(<ReliabilityChart data={null} />);

    expect(container.querySelector(".rel-empty")).not.toBeNull();
  });

  it("still draws a curve the server did return", () => {
    stubChartLayout();

    const { container } = renderWithQuery(<ReliabilityChart data={CURVE as never} />);

    expect(container.querySelector(".rel-empty")).toBeNull();
    expect(container.querySelector(".rel-chart-wrap")).not.toBeNull();
  });
});
