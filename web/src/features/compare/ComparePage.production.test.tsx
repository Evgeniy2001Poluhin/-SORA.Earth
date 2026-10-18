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
import { callsOf, stubJson } from "@/test/http";

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

/** Two different answers, routed by the project each request names, so the
 *  two columns really differ. Scores are chosen so B leads by exactly 6.5. */
const SOLAR = {
  total_score: 63.5, environment_score: 58.5, social_score: 71.5, economic_score: 60.5,
  risk_level: "Medium", success_probability: 67.5, recommendations: [], region: "EU",
};
const WIND = {
  total_score: 70, environment_score: 66, social_score: 74, economic_score: 70,
  risk_level: "Low", success_probability: 72, recommendations: [], region: "EU",
};

function stubByProjectName(answers: Record<string, unknown>) {
  const stub = vi.fn(async (_input: unknown, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body ?? "{}")) as { project_name?: string };
    return new Response(JSON.stringify(answers[body.project_name ?? ""] ?? {}), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  });
  globalThis.fetch = stub as unknown as typeof fetch;
  return stub;
}

describe("ComparePage's difference is an ESG index, not a verdict", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("names the higher ESG index instead of calling the project a WINNER", async () => {
    stubByProjectName({ "Solar Farm": SOLAR, "Offshore Wind": WIND });

    const { container } = renderWithQuery(<ComparePage />);
    await runBoth();

    await waitFor(
      () => expect(screen.getByText("Higher ESG index: Offshore Wind by Δ +6.5")).toBeInTheDocument(),
      { timeout: 3000 },
    );
    const badge = container.querySelector(".cmp-winner-badge")?.textContent ?? "";
    expect(badge).toContain("Higher ESG index");
    expect(container.textContent ?? "").not.toMatch(/WINNER/);
    expect(screen.queryByText(/leads by/)).not.toBeInTheDocument();
  });

  it("says what the difference measures and nothing about success", async () => {
    stubByProjectName({ "Solar Farm": SOLAR, "Offshore Wind": WIND });

    const { container } = renderWithQuery(<ComparePage />);
    await runBoth();

    await waitFor(() => expect(container.querySelector(".cmp-diff-note")).not.toBeNull(), {
      timeout: 3000,
    });
    expect(container.querySelector(".cmp-diff-note")?.textContent).toBe(
      "Positive Δ means Project B has the higher ESG index.",
    );
    expect(container.querySelector(".cmp-diff")?.textContent ?? "").not.toMatch(/favours|will succeed/);
  });

  it("a tie is a tie on the index, with no column marked as the winner", async () => {
    stubByProjectName({ "Solar Farm": SOLAR, "Offshore Wind": SOLAR });

    const { container } = renderWithQuery(<ComparePage />);
    await runBoth();

    await waitFor(() => expect(screen.getByText("Tie on ESG index")).toBeInTheDocument(), {
      timeout: 3000,
    });
    expect(container.querySelector(".cmp-winner-badge")).toBeNull();
  });
});

describe("ComparePage's hero makes only the claims the page checks", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("does not promise the same model or the same benchmarks", () => {
    const { container } = renderWithQuery(<ComparePage />);
    const hero = container.querySelector(".cmp-hero")?.textContent ?? "";

    // The guarantee the old copy made, verbatim. The honest sentence that
    // replaces it also contains the words "same model", so match the claim.
    expect(hero).not.toMatch(/Same model, same benchmarks/);
  });

  it("says the page does not check that both projects used the same model", () => {
    const { container } = renderWithQuery(<ComparePage />);
    const hero = container.querySelector(".cmp-hero")?.textContent ?? "";

    expect(hero).toMatch(/does not check that both were scored by the same model/);
  });

  it("sends each project as its own separate request", async () => {
    const stub = stubByProjectName({ "Solar Farm": SOLAR, "Offshore Wind": WIND });

    renderWithQuery(<ComparePage />);
    await runBoth();

    await waitFor(
      () => expect(callsOf(stub).filter((c) => c.url.endsWith("/evaluate")).length).toBeGreaterThanOrEqual(2),
      { timeout: 3000 },
    );
    const evaluateBodies = callsOf(stub)
      .filter((c) => c.url.endsWith("/evaluate"))
      .map((c) => (c.body as { project_name: string }).project_name);
    // One project per request, never both in one body.
    expect(new Set(evaluateBodies)).toEqual(new Set(["Solar Farm", "Offshore Wind"]));
  });
});
