/**
 * The map against a successful-but-empty answer, and against a legitimately
 * empty one (#236).
 *
 * Two different failures live here. `{}` crashed on `data.countries.filter`,
 * the same shape as the rest of the sweep. But `{countries: []}` — a perfectly
 * valid answer meaning "no countries yet" — crashed on `countries[0].name`,
 * which needs no malformed payload at all.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("@/api/mock", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/mock")>();
  return { ...actual, isMock: false };
});

import MapPage from "./MapPage";
import { isMock } from "@/api/mock";
import { renderWithQuery } from "@/test/utils";
import { stubJson, stubStatus, stubTransportError } from "@/test/http";

const render = () => renderWithQuery(<MemoryRouter><MapPage /></MemoryRouter>);

describe("the guard on these tests", () => {
  it("really is in production mode", () => {
    expect(isMock).toBe(false);
  });
});

describe("MapPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders nothing rather than crashing on an empty 200", async () => {
    // A guarded component returning null and a crashed one both leave an
    // empty container, so the DOM cannot tell them apart -- the first version
    // of this test asserted on the DOM and survived its own mutation, which
    // is how it was caught. The thrown error is the only observable
    // difference, so that is what is asserted.
    stubJson({});
    const thrown: string[] = [];
    vi.spyOn(console, "error").mockImplementation((...args) => {
      thrown.push(args.map(String).join(" "));
    });

    render();
    await new Promise((resolve) => setTimeout(resolve, 400));

    expect(thrown.filter((m) => /TypeError/.test(m))).toEqual([]);
  });

  it("stays up when the server legitimately reports no countries", async () => {
    stubJson({ total_countries: 0, countries: [], bands: {} });

    const { container } = render();

    // Not the `.map-page` selector: the loading state carries that class
    // too, so the wait would resolve before the payload ever arrived.
    await waitFor(() => expect(container.textContent ?? "").toContain("0 countries"), {
      timeout: 3000,
    });
    const text = container.textContent ?? "";
    // Averaging an empty list divides by zero.
    expect(text).not.toContain("NaN");
  });

  it("still shows the leader stats when countries are present", async () => {
    stubJson({
      total_countries: 2,
      countries: [
        { name: "Sweden", esg: 81.5, band: "leader", code: "SE", lat: 60, lon: 15 },
        { name: "Chile", esg: 62.5, band: "mid", code: "CL", lat: -33, lon: -70 },
      ],
      bands: {},
    });

    const { container } = render();

    await waitFor(() => expect(container.textContent ?? "").toContain("Sweden"), { timeout: 3000 });
    expect(container.textContent ?? "").toContain("72");
  });
});

/** The value beside a stat's label: `statValue(c, "avg ESG")` -> "72". */
function statValue(container: HTMLElement, label: string): string | null {
  const em = Array.from(container.querySelectorAll(".map-stats .stat em")).find((e) => e.textContent === label);
  return em?.parentElement?.querySelector("b")?.textContent ?? null;
}
const topNames = (container: HTMLElement) =>
  Array.from(container.querySelectorAll(".top-list li b")).map((b) => b.textContent);
const topRows = (container: HTMLElement) =>
  Array.from(container.querySelectorAll(".top-list li")).map((li) => li.textContent ?? "");
const scopeLines = (container: HTMLElement) =>
  Array.from(container.querySelectorAll(".map-scope")).map((p) => p.textContent ?? "");

/** The pinned-data facts the backend sends beside the countries (C8 part 3). */
const PINNED = {
  table_available: true,
  snapshot_date: "2026-10-18",
  earliest_expiry: "2027-01-01",
  expiry_notice_days: 60,
};
const SCORED_ONE = {
  total_countries: 1, bands: {},
  countries: [{
    name: "Sweden", esg: 81.5, band: "leader", code: "SE", lat: 60, lon: 15,
    esg_status: "ok", esg_source: "pinned_table", missing_inputs: [],
  }],
};

describe("MapPage pinned data (C8 part 3)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the snapshot date and the earliest expiry", async () => {
    stubJson({ ...SCORED_ONE, ...PINNED, days_to_earliest_expiry: 74, expiry_notice: false });

    const { container } = render();
    await waitFor(() => expect(container.textContent ?? "").toContain("Pinned data"), { timeout: 3000 });

    const text = container.textContent ?? "";
    expect(text).toContain("snapshot 2026-10-18");
    expect(text).toContain("the earliest input expires 2027-01-01");
  });

  it("shows no expiry notice more than 60 days out", async () => {
    stubJson({ ...SCORED_ONE, ...PINNED, days_to_earliest_expiry: 74, expiry_notice: false });

    const { container } = render();
    await waitFor(() => expect(container.textContent ?? "").toContain("Pinned data"), { timeout: 3000 });

    expect(container.querySelector('[role="alert"]')).toBeNull();
  });

  it("shows a notice with the date and the days left within 60 days", async () => {
    stubJson({ ...SCORED_ONE, ...PINNED, days_to_earliest_expiry: 30, expiry_notice: true });

    const { container } = render();
    await waitFor(() => expect(container.querySelector('[role="alert"]')).not.toBeNull(), { timeout: 3000 });

    expect(container.querySelector('[role="alert"]')?.textContent ?? "")
      .toContain("The pinned data expires on 2027-01-01, in 30 days.");
  });

  it("says the data has expired once the date has passed", async () => {
    stubJson({ ...SCORED_ONE, ...PINNED, days_to_earliest_expiry: -2, expiry_notice: true });

    const { container } = render();
    await waitFor(() => expect(container.querySelector('[role="alert"]')).not.toBeNull(), { timeout: 3000 });

    expect(container.querySelector('[role="alert"]')?.textContent ?? "").toContain("expired on 2027-01-01");
  });

  it("says no pinned data is loaded, and gives no snapshot, when the table is unavailable", async () => {
    stubJson({
      ...SCORED_ONE, table_available: false, table_unavailable_reason: "manifest.json is not the manifest pinned",
      snapshot_date: null, earliest_expiry: null, days_to_earliest_expiry: null, expiry_notice: false,
    });

    const { container } = render();
    await waitFor(() => expect(container.textContent ?? "").toContain("No pinned data is loaded"), { timeout: 3000 });

    expect(container.textContent ?? "").not.toContain("snapshot");
  });
});

// Shapes as /map/countries sends them (C8).
const ICELAND_INCOMPLETE = {
  name: "Iceland", esg: null, band: null, code: "IS", lat: 65, lon: -19,
  esg_status: "incomplete", esg_source: null, missing_inputs: ["gini_index", "gov_effectiveness"],
  renewable_share_pct: null, co2_intensity_t_per_capita: null,
};
const SWEDEN_OK = {
  name: "Sweden", esg: 81.5, band: "leader", code: "SE", lat: 60, lon: 15,
  esg_status: "ok", esg_source: "pinned_table", missing_inputs: [],
  renewable_share_pct: 60.1, co2_intensity_t_per_capita: null,
};
const CHILE_OK = {
  name: "Chile", esg: 62.5, band: "developing", code: "CL", lat: -33, lon: -70,
  esg_status: "ok", esg_source: "pinned_table", missing_inputs: [],
  renewable_share_pct: null, co2_intensity_t_per_capita: null,
};
const ZERO_OK = {
  name: "Zeroland", esg: 0, band: "lagging", code: "ZZ", lat: 0, lon: 0,
  esg_status: "ok", esg_source: "pinned_table", missing_inputs: [],
  renewable_share_pct: null, co2_intensity_t_per_capita: null,
};

describe("MapPage scores (C8)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("leaves an incomplete country out of the top-10, the average and the leaders", async () => {
    stubJson({ total_countries: 3, bands: {}, countries: [ICELAND_INCOMPLETE, SWEDEN_OK, CHILE_OK] });

    const { container } = render();
    await waitFor(() => expect(topNames(container).length).toBeGreaterThan(0), { timeout: 3000 });

    expect(topNames(container)).toEqual(["Sweden", "Chile"]);
    expect(statValue(container, "avg ESG"), "(81.5 + 62.5) / 2, without Iceland").toBe("72");
    expect(statValue(container, "leaders"), "Iceland has no score, so it is not a leader").toBe("1");
    expect(container.textContent ?? "").not.toContain("Iceland");
  });

  it("states N as x of y countries, with the ok count, in the header and above the top-10", async () => {
    stubJson({ total_countries: 3, bands: {}, countries: [ICELAND_INCOMPLETE, SWEDEN_OK, CHILE_OK] });

    const { container } = render();
    await waitFor(() => expect(topNames(container).length).toBeGreaterThan(0), { timeout: 3000 });

    expect(container.textContent ?? "").toContain("N = 2 of 3 countries have a score");
    // Only the lines that state N: the pinned-data line is also a scope line now (C8 part 3).
    const lines = scopeLines(container).filter((line) => line.includes("N = "));
    expect(lines).toHaveLength(2);
    for (const line of lines) {
      expect(line).toContain("N = 2 of 3 countries (2 ok); 1 incomplete, excluded");
    }
  });

  it("a real 0 score is scored: it is ranked and counted in the average", async () => {
    stubJson({ total_countries: 2, bands: {}, countries: [SWEDEN_OK, ZERO_OK] });

    const { container } = render();
    await waitFor(() => expect(topNames(container).length).toBeGreaterThan(0), { timeout: 3000 });

    expect(topNames(container)).toEqual(["Sweden", "Zeroland"]);
    expect(statValue(container, "avg ESG"), "(81.5 + 0) / 2, rounded").toBe("41");
  });

  it("a null score is never averaged as 0", async () => {
    stubJson({ total_countries: 2, bands: {}, countries: [SWEDEN_OK, ICELAND_INCOMPLETE] });

    const { container } = render();
    await waitFor(() => expect(topNames(container).length).toBeGreaterThan(0), { timeout: 3000 });

    expect(statValue(container, "avg ESG"), "the average of Sweden alone, not (81.5 + 0) / 2").toBe("82");
  });

  it("ranks an ok score without a status suffix", async () => {
    stubJson({ total_countries: 2, bands: {}, countries: [SWEDEN_OK, CHILE_OK] });

    const { container } = render();
    await waitFor(() => expect(topNames(container).length).toBeGreaterThan(0), { timeout: 3000 });

    const chile = topRows(container).find((t) => t.includes("Chile")) ?? "";
    expect(chile).not.toContain("mixed");
    expect(chile).toContain("62.5");
  });

  it("with no scored country, shows no average and does not print NaN or null", async () => {
    stubJson({ total_countries: 1, bands: {}, countries: [ICELAND_INCOMPLETE] });

    const { container } = render();
    await waitFor(() => expect(container.textContent ?? "").toContain("no complete score"), { timeout: 3000 });

    const text = container.textContent ?? "";
    expect(text).toContain("no complete score: N = 0 of 1 countries");
    expect(text).not.toMatch(/NaN|null|undefined/);
    expect(topNames(container)).toEqual([]);
  });

  it("shows unavailable on a failed request, not an incomplete score", async () => {
    stubStatus(500, "upstream said no");

    const { container } = render();
    await waitFor(() => expect(container.textContent ?? "").toContain("unavailable"), { timeout: 3000 });

    expect(container.textContent ?? "").not.toContain("incomplete");
  });

  it("shows unavailable on a transport error too", async () => {
    stubTransportError();

    const { container } = render();
    await waitFor(() => expect(container.textContent ?? "").toContain("unavailable"), { timeout: 3000 });
  });
});
