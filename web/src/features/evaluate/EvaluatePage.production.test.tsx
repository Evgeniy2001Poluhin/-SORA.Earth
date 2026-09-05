/**
 * EvaluatePage against a successful-but-empty answer (#236).
 *
 * Found while writing the production-mode coverage for #218: a 200 carrying
 * `{}` passed the `!result` guard (an empty object is truthy), reached
 * `fmtNum(result.budget_usd)` with `undefined`, and took the entire page
 * down — `Cannot read properties of undefined (reading 'toLocaleString')`,
 * a blank white screen where the ESG score belongs.
 *
 * That is the loudest face of the same defect `UncertaintyCard` (0.0%) and
 * `WhatIf` (+0.00 bars) showed quietly. All three come from one missing
 * distinction: **absent** and **empty** are different answers.
 *
 * The formatter in `src/lib/format.ts` is deliberately left strict rather
 * than taught to print a dash for `undefined`. A crash is loud; a dash where
 * a score belongs is the quiet kind of wrong (#216). The fix is to stop
 * calling it with nothing.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

vi.mock("@/api/mock", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/mock")>();
  return { ...actual, isMock: false };
});

import { EvaluatePage } from "./EvaluatePage";
import { isMock } from "@/api/mock";
import { renderWithQuery } from "@/test/utils";
import { stubJson, stubStatus } from "@/test/http";

const renderPage = () => renderWithQuery(<MemoryRouter><EvaluatePage /></MemoryRouter>);
const runEvaluation = async () => {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Run evaluation" }));
};

describe("the guard on these tests", () => {
  it("really is in production mode", () => {
    expect(isMock).toBe(false);
  });
});

describe("EvaluatePage when the API answers with nothing", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("keeps the page standing instead of blanking it", async () => {
    stubJson({});

    const { container } = renderPage();
    await runEvaluation();
    await new Promise((resolve) => setTimeout(resolve, 500));

    // The regression this file exists for: the whole page used to unmount.
    expect(container.textContent ?? "").not.toBe("");
    expect(container.querySelector(".ev-result")).not.toBeNull();
  });

  it("shows the empty state rather than a score of zero", async () => {
    stubJson({});

    renderPage();
    await runEvaluation();

    await waitFor(() => expect(screen.getByText(/No evaluation yet/i)).toBeInTheDocument(), {
      timeout: 3000,
    });
  });

  it("shows the empty state when the request fails outright", async () => {
    stubStatus(500);

    renderPage();
    await runEvaluation();
    await new Promise((resolve) => setTimeout(resolve, 500));

    expect(screen.getByText(/No evaluation yet/i)).toBeInTheDocument();
  });

  it("renders the score when the server actually sends one", async () => {
    // The other half: the guard must not suppress a real answer. 61.5 is
    // unlike anything the canned payload produces.
    stubJson({
      total_score: 61.5,
      environment_score: 55.5,
      social_score: 70.5,
      economic_score: 58.5,
      risk_level: "Medium",
      success_probability: 66.5,
      recommendations: [],
      region: "EU",
    });

    renderPage();
    await runEvaluation();

    await waitFor(() => expect(screen.queryByText(/No evaluation yet/i)).not.toBeInTheDocument(), {
      timeout: 3000,
    });
    expect(screen.getByText("Medium risk")).toBeInTheDocument();
  });
});
