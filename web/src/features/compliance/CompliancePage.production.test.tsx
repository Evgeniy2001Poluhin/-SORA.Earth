/**
 * The CSRD/ESRS panel against a successful-but-empty answer (#236).
 *
 * `{mut.data && (...)}` is the same guard shape as the rest of the sweep, and
 * `mut.data.overall_readiness.toFixed(0)` inside it took the page down on an
 * empty 200.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("@/api/mock", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/mock")>();
  return { ...actual, isMock: false };
});

import { CompliancePage } from "./CompliancePage";
import { isMock } from "@/api/mock";
import { renderWithQuery } from "@/test/utils";
import { stubJson } from "@/test/http";

const runCheck = async () => {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /Run CSRD\/ESRS check/i }));
};

describe("the guard on these tests", () => {
  it("really is in production mode", () => {
    expect(isMock).toBe(false);
  });
});

describe("CompliancePage when the API answers with nothing", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("keeps the form standing instead of blanking the page", async () => {
    stubJson({});

    const { container } = renderWithQuery(<CompliancePage />);
    await runCheck();
    await new Promise((resolve) => setTimeout(resolve, 500));

    expect(container.textContent ?? "").not.toBe("");
    expect(screen.getByRole("button", { name: /Run CSRD\/ESRS check/i })).toBeInTheDocument();
    expect(container.querySelector(".cmp-result")).toBeNull();
  });

  it("renders the readiness the server actually returned", async () => {
    stubJson({
      project_name: "Solar Farm",
      overall_readiness: 73.5,
      status: "partial",
      framework_version: "ESRS 2024",
      audit_ready: false,
      categories: { E1: { score: 70, status: "partial", gaps: [] } },
      recommended_actions: [],
    });

    const { container } = renderWithQuery(<CompliancePage />);
    await runCheck();

    await waitFor(() => expect(container.querySelector(".cmp-result")).not.toBeNull(), {
      timeout: 3000,
    });
    expect(container.textContent ?? "").toContain("74");
    expect(container.textContent ?? "").toContain("Solar Farm");
  });

  it("takes the empty state on a partial payload the section cannot render", async () => {
    // A score without categories still threw on `Object.entries`, so the
    // guard names every field the section reads, not just the headline one.
    stubJson({ project_name: "Solar Farm", overall_readiness: 73.5, status: "partial" });

    const { container } = renderWithQuery(<CompliancePage />);
    await runCheck();
    await new Promise((resolve) => setTimeout(resolve, 500));

    expect(container.querySelector(".cmp-result")).toBeNull();
    expect(screen.getByRole("button", { name: /Run CSRD\/ESRS check/i })).toBeInTheDocument();
  });

  it("drops a category with no score rather than crashing on it", async () => {
    // Item level: `v.gaps.length` and `v.score.toFixed` threw the same way
    // one layer inside a payload that was otherwise well-formed.
    stubJson({
      project_name: "Solar Farm",
      overall_readiness: 73.5,
      status: "partial",
      framework_version: "ESRS 2024",
      audit_ready: false,
      categories: { E1: { score: 70, status: "partial" }, E2: {} },
      recommended_actions: [],
    });

    const { container } = renderWithQuery(<CompliancePage />);
    await runCheck();

    await waitFor(() => expect(container.querySelector(".cmp-result")).not.toBeNull(), {
      timeout: 3000,
    });
    expect(container.querySelectorAll(".cat")).toHaveLength(1);
    expect(container.textContent ?? "").not.toContain("NaN");
  });
});
