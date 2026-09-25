/**
 * The history page when the request fails, and when the history is empty.
 *
 * "Absent" and "empty" are different answers (#236), and this page gave the
 * same one to both. `/history` answers 200 with `items` and `total` every time
 * (app/api/evaluate.py, `get_history`); when the request fails there is no
 * answer at all. The header read `query.data?.total ?? 0` and averaged an empty
 * list to `0`, and the empty state was gated only on "not loading" -- so a
 * failed request printed "Failed to load", then "0 evaluations · avg score
 * 0.0", then "No evaluations match the current filters." Three claims, and the
 * first was the only true one.
 *
 * The average had the same problem on a real, empty answer: the mean of no
 * rows is not 0.0, it is nothing.
 *
 * Measured with transport stubs, not deduced.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("@/api/mock", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/mock")>();
  return { ...actual, isMock: false };
});

import HistoryPage from "./HistoryPage";
import { isMock } from "@/api/mock";
import { renderWithQuery } from "@/test/utils";
import { stubJson, stubStatus } from "@/test/http";

const renderPage = () => renderWithQuery(<MemoryRouter><HistoryPage /></MemoryRouter>);

/** The header line: "N evaluations · avg score X · page P of Q". */
const header = (c: HTMLElement) => c.querySelector(".ev-meta")?.textContent ?? "";

const row = (id: number, total_score: number | null) => ({
  id, created_at: "2026-09-20T10:00:00Z", region: "Europe", total_score,
  // A percentage, as `/history` returns it -- the same scale as `/evaluate`
  // (tests/test_history_probability_scale.py pins that on the backend). This
  // fixture was a fraction, `total_score / 100 + 0.05`, which the API never
  // sends -- and on a fraction the page's `* 100` looked right.
  success_probability: total_score == null ? null : Math.min(100, total_score + 5),
  risk_level: "LOW", budget: 150000, duration_months: 18,
});

describe("the guard on these tests", () => {
  it("really is in production mode", () => {
    expect(isMock).toBe(false);
  });
});

describe("HistoryPage when the request fails", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    stubStatus(503);
  });

  it("does not report zero evaluations it never counted", async () => {
    const { container } = renderPage();
    // Wait for the answer, not for the label: the header renders before the
    // query settles, and reading it early reads the fallback.
    await waitFor(() => expect(container.textContent).toContain("Failed to load"));
    expect(header(container)).not.toMatch(/\b0 evaluations/);
  });

  it("does not print an average score of nothing", async () => {
    const { container } = renderPage();
    await waitFor(() => expect(container.textContent).toContain("Failed to load"));
    expect(header(container)).not.toContain("avg score 0.0");
  });

  it("does not say that no evaluations match the filters", async () => {
    const { container } = renderPage();
    await waitFor(() => expect(container.textContent).toContain("Failed to load"));
    expect(container.textContent).not.toContain("No evaluations match the current filters.");
  });
});

describe("HistoryPage when the history is genuinely empty", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    stubJson({ items: [], total: 0, limit: 20, offset: 0 });
  });

  it("still says zero, because the server counted zero", async () => {
    // Control. A fix that dashes every number would pass the tests above and
    // hide a true "0 evaluations" -- which is exactly what an operator needs
    // to see on a fresh deployment.
    const { container } = renderPage();
    // Wait on the empty-state line, which appears only once the answer is in.
    // Waiting on "0 evaluations" would be satisfied by the very fallback this
    // file is about -- it is on screen before the request settles -- and was:
    // the first version of this control read the loading skeleton and failed.
    await waitFor(() => expect(container.textContent).toContain("No evaluations match the current filters."));
    expect(header(container)).toMatch(/\b0 evaluations/);
  });

  it("does not average an empty list to 0.0", async () => {
    const { container } = renderPage();
    await waitFor(() => expect(container.textContent).toContain("No evaluations match the current filters."));
    expect(header(container)).not.toContain("avg score 0.0");
    expect(header(container)).toContain("avg score —");
  });
});

describe("HistoryPage with real rows", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the count and the average the server's rows give", async () => {
    // Control: the true values are still on screen.
    stubJson({ items: [row(1, 70), row(2, 81)], total: 57, limit: 20, offset: 0 });
    const { container } = renderPage();
    await waitFor(() => expect(header(container)).toMatch(/\b57 evaluations/));
    expect(header(container)).toContain("avg score 75.5");
  });

  it("averages only the rows that carry a score", async () => {
    // `evaluations.total_score` is a nullable column. Whether production holds
    // such a row could not be determined from the repository, so the page is
    // made true either way: `a + null` used to count the row as a zero, and
    // `null.toFixed` in the row took the page down.
    stubJson({ items: [row(1, 70), row(2, null), row(3, 80)], total: 3, limit: 20, offset: 0 });
    const { container } = renderPage();
    await waitFor(() => expect(header(container)).toMatch(/\b3 evaluations/));
    expect(header(container)).toContain("avg score 75.0");
    const cells = Array.from(container.querySelectorAll(".hist-item")).map((b) => b.textContent ?? "");
    expect(cells).toHaveLength(3);
    expect(cells[1]).toContain("—");
    expect(cells[1]).not.toContain("0%");
  });
});

describe("the success probability column", () => {
  it("shows the percentage the API returns, not a hundred times it", async () => {
    // 27.5 is what /evaluate and /history return for a project the model rates
    // at 27.5 %. The page multiplied it by 100 and printed "2750%".
    stubJson({ items: [{ ...row(1, 49.06), success_probability: 27.5 }], total: 1, limit: 20, offset: 0 });
    const { container } = renderPage();
    await waitFor(() => expect(container.querySelector(".hist-item")).not.toBeNull());
    // Whole cells, not the row's text: cells concatenate ("49.1" + "28%" reads
    // "49.128%"), so a substring check would also pass on "128%".
    const cells = Array.from(container.querySelector(".hist-item")!.children).map((c) => c.textContent);
    expect(cells).toContain("28%");
    expect(cells).not.toContain("2750%");
  });
});
