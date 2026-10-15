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
import { stubJson } from "@/test/http";

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
const scopeLines = (container: HTMLElement) =>
  Array.from(container.querySelectorAll(".map-scope")).map((p) => p.textContent ?? "");

// Shapes as /map/countries sends them: a static profile carries the formula
// values the backend derives from its ESG score; a live country carries its own.
const ICELAND_STATIC = {
  name: "Iceland", esg: 86, band: "leader", code: "IS", lat: 65, lon: -19,
  esg_source: "hardcoded", renewable_share_pct: 81.7, co2_intensity_t_per_capita: 4.52,
};
const JAPAN_STATIC = {
  name: "Japan", esg: 77, band: "advanced", code: "JP", lat: 36, lon: 138,
  esg_source: "hardcoded", renewable_share_pct: 73.2, co2_intensity_t_per_capita: 6.1,
};
const SWEDEN_LIVE = {
  name: "Sweden", esg: 81.5, band: "leader", code: "SE", lat: 60, lon: 15,
  esg_source: "world_bank_live", renewable_share_pct: 60.1, co2_intensity_t_per_capita: 3.6,
};
// Live ESG, but no renewables or CO2 in the payload.
const CHILE_LIVE_NO_PHYSICAL = {
  name: "Chile", esg: 62.5, band: "developing", code: "CL", lat: -33, lon: -70,
  esg_source: "world_bank_live", renewable_share_pct: null, co2_intensity_t_per_capita: null,
};

describe("MapPage sources (F070-F073)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("leaves a country with no live source out of the top-10, the average and the leaders", async () => {
    // Iceland is the highest score on the page and a static reference value.
    // Sweden and Chile are live, so every aggregate is over those two. Chile
    // has no renewables or CO2 and stays in: its ESG score is itself live.
    stubJson({ total_countries: 3, bands: {}, countries: [ICELAND_STATIC, SWEDEN_LIVE, CHILE_LIVE_NO_PHYSICAL] });

    const { container } = render();
    await waitFor(() => expect(topNames(container).length).toBeGreaterThan(0), { timeout: 3000 });

    expect(topNames(container)).toEqual(["Sweden", "Chile"]);
    expect(statValue(container, "avg ESG"), "(81.5 + 62.5) / 2, without Iceland").toBe("72");
    expect(statValue(container, "leaders"), "Iceland is a leader, but static").toBe("1");
    expect(container.textContent ?? "").not.toContain("Iceland");
  });

  it("states which source the aggregates use and how many countries that is", async () => {
    stubJson({ total_countries: 3, bands: {}, countries: [ICELAND_STATIC, SWEDEN_LIVE, CHILE_LIVE_NO_PHYSICAL] });

    const { container } = render();
    await waitFor(() => expect(topNames(container).length).toBeGreaterThan(0), { timeout: 3000 });

    // Once in the header and once above the top-10: both state the same N.
    const lines = scopeLines(container);
    expect(lines).toHaveLength(2);
    for (const line of lines) {
      expect(line).toContain("live source only (world_bank_live): N = 2 of 3 countries");
    }
  });

  it("says so, and uses every country, when all of them are static reference values", async () => {
    stubJson({ total_countries: 2, bands: {}, countries: [ICELAND_STATIC, JAPAN_STATIC] });

    const { container } = render();
    await waitFor(() => expect(topNames(container).length).toBeGreaterThan(0), { timeout: 3000 });

    expect(scopeLines(container)[0]).toContain("static reference values only: N = 2 countries");
    expect(topNames(container)).toEqual(["Iceland", "Japan"]);
    expect(statValue(container, "avg ESG")).toBe("82");
  });

  it("says mixed when a country's source is missing, rather than guessing its kind", async () => {
    // Without esg_source the page cannot tell live from static, so it uses
    // every country and says that it could not separate them.
    stubJson({
      total_countries: 2,
      bands: {},
      countries: [
        { name: "Sweden", esg: 81.5, band: "leader", code: "SE", lat: 60, lon: 15 },
        { name: "Chile", esg: 62.5, band: "developing", code: "CL", lat: -33, lon: -70 },
      ],
    });

    const { container } = render();
    await waitFor(() => expect(topNames(container).length).toBeGreaterThan(0), { timeout: 3000 });

    expect(scopeLines(container)[0]).toContain("mixed or unlabelled sources: N = 2 countries");
    expect(statValue(container, "avg ESG")).toBe("72");
  });
});
