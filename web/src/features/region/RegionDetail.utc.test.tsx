/**
 * RegionDetail formatPeriod UTC test in a dedicated file.
 *
 * Node re-reads the zone whenever process.env.TZ changes, so setting it below
 * affects every Date created afterwards -- the render in the test. (ESM imports
 * are hoisted, so the assignment runs after them, not before.) The previous
 * value is restored after the file, so no other test runs in this zone.
 *
 * period_start "2024-01-01T00:00:00+00:00" is midnight UTC on Jan 1 2024, which
 * is 16:00 PST on Dec 31 2023. With getFullYear() the viewer sees "2023-2024";
 * with getUTCFullYear() they see "2024".
 */

import { afterAll, describe, expect, it, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";

vi.mock("@/api/mock", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/mock")>();
  return { ...actual, isMock: false };
});

import RegionDetail from "./RegionDetail";
import { renderWithQuery } from "@/test/utils";
import { stubJson } from "@/test/http";

const previousTZ = process.env.TZ;
process.env.TZ = "America/Los_Angeles";
afterAll(() => {
  if (previousTZ === undefined) delete process.env.TZ;
  else process.env.TZ = previousTZ;
});

describe("formatPeriod reads years in UTC", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("shows 2024 for period_start 2024-01-01T00:00:00+00:00 even when TZ is west of UTC", async () => {
    // Verify TZ is set
    expect(process.env.TZ).toBe("America/Los_Angeles");

    const response = {
      region: { code: "RU-MOW", name: "RU-MOW" },
      esg: { score: 89.25, e_score: 89.0, s_score: 86.5, g_score: 93.5 },
      confidence: 0.67,
      sources_used: [],
      sources_missing: [],
      model_version: null,
      computed_at: "2026-09-02T20:26:28.091744+00:00",
      features: null,
      inputs: [
        {
          source: "rosstat",
          indicator: "unemployment_rate",
          value: 1.9,
          unit: "%",
          temporal_kind: "period",
          period_start: "2024-01-01T00:00:00+00:00",
          period_end: "2024-12-31T00:00:00+00:00",
          delivered_at: "2026-09-27T12:00:00Z",
          source_revision: "snapshot_2024_v2",
          missing: false,
        },
      ],
      generated_at: "2026-09-26T16:19:58.225816+00:00",
      score_kind: "structural",
      score_vintage: "2024",
    };

    stubJson(response);

    renderWithQuery(
      <MemoryRouter initialEntries={["/region/RU-MOW"]}>
        <Routes>
          <Route path="/region/:code" element={<RegionDetail />} />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByText("89.3", { exact: false });

    // The period column must show "2024", not "2023-2024" or "2023"
    const page = document.body.textContent ?? "";
    expect(page).toContain("2024");

    // Stronger assertion: the table row with "Уровень безработицы" must have "2024" in its period cell
    const table = document.querySelector(".rd-table");
    expect(table).toBeInTheDocument();
    const rows = Array.from(table?.querySelectorAll("tbody tr") ?? []);
    const unempRow = rows.find((r) => r.textContent?.includes("Уровень безработицы"));
    expect(unempRow).toBeDefined();
    expect(unempRow?.textContent).toContain("2024");
    // Must NOT show 2023
    expect(unempRow?.textContent).not.toContain("2023");
  });
});
