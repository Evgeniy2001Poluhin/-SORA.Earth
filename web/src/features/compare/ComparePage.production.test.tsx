/**
 * The A/B compare page against a successful-but-empty answer (#236).
 *
 * The third crash of the same shape: `if (!r)` guarded `undefined` while `{}`
 * went through to `r.total_score.toFixed(1)`, and the whole page unmounted —
 * a blank screen where two projects should be side by side.
 *
 * Measured with a transport stub returning 200 and `{}`.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("@/api/mock", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/mock")>();
  return { ...actual, isMock: false };
});

import { ComparePage } from "./ComparePage";
import { isMock } from "@/api/mock";
import { renderWithQuery } from "@/test/utils";
import { stubJson } from "@/test/http";

const runBoth = async () => {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /Run both/i }));
};

describe("the guard on these tests", () => {
  it("really is in production mode", () => {
    expect(isMock).toBe(false);
  });
});

describe("ComparePage when the API answers with nothing", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("keeps the page standing instead of blanking it", async () => {
    stubJson({});

    const { container } = renderWithQuery(<ComparePage />);
    await runBoth();
    await new Promise((resolve) => setTimeout(resolve, 600));

    expect(container.textContent ?? "").not.toBe("");
    expect(screen.getAllByText(/Press Run to evaluate/i).length).toBe(2);
  });

  it("renders both scores when the server actually sends them", async () => {
    // A guard that hides real results would be the worse bug.
    stubJson({
      total_score: 63.5,
      environment_score: 58.5,
      social_score: 71.5,
      economic_score: 60.5,
      risk_level: "Medium",
      success_probability: 67.5,
      recommendations: [],
      region: "EU",
    });

    const { container } = renderWithQuery(<ComparePage />);
    await runBoth();

    await waitFor(() => expect(screen.queryAllByText(/Press Run to evaluate/i).length).toBe(0), {
      timeout: 3000,
    });
    expect(container.textContent ?? "").toContain("63.5");
  });
});
