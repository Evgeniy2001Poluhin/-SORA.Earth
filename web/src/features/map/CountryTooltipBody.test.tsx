/**
 * The country tooltip shows what each number is and where it came from.
 *
 * F070: `esg_source` was dropped, so a static reference value read as live.
 * F071/F072: for a static profile the backend fills renewables and CO2 per
 * capita with formulas of the ESG score, and the page printed them as measured.
 *
 * Tested through the extracted body: react-leaflet's Tooltip is a layer and is
 * not in the DOM until a marker is opened in a real map.
 */
import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";

import CountryTooltipBody from "./CountryTooltipBody";
import type { MapCountry } from "./countryData";

// As the backend sends a static profile: the formulas, not measurements.
const ICELAND_STATIC: MapCountry = {
  code: "IS", name: "Iceland", lat: 65, lon: -19, esg: 86, band: "leader",
  esg_source: "hardcoded", renewable_share_pct: 81.7, co2_intensity_t_per_capita: 4.52,
};
const SWEDEN_LIVE: MapCountry = {
  code: "SE", name: "Sweden", lat: 60, lon: 15, esg: 81.5, band: "leader",
  esg_source: "world_bank_live", renewable_share_pct: 60.1, co2_intensity_t_per_capita: 3.6,
};
const CHILE_LIVE_MISSING: MapCountry = {
  code: "CL", name: "Chile", lat: -33, lon: -70, esg: 62.5, band: "developing",
  esg_source: "world_bank_live", renewable_share_pct: null, co2_intensity_t_per_capita: null,
};
const UNLABELLED: MapCountry = {
  code: "XX", name: "Nowhere", lat: 0, lon: 0, esg: 50, band: "emerging",
  renewable_share_pct: 50, co2_intensity_t_per_capita: 2,
};

// The score from the BENCHMARKS table, with the formula-like values a static
// profile could carry: the page must not show them either.
const STATIC_BENCHMARK_COUNTRY: MapCountry = {
  code: "JP", name: "Japan", lat: 36, lon: 138, esg: 77, band: "advanced",
  esg_source: "static_benchmark", renewable_share_pct: 73.2, co2_intensity_t_per_capita: 6.1,
};

// As /map/countries sends them in esg_source_meanings.
const MEANINGS: Record<string, string> = {
  world_bank_live: "Score computed from a fresh live-cache entry (under 24h old).",
  static_benchmark: "Live cache empty or stale. Score computed from the static BENCHMARKS row.",
  hardcoded: "No external data for this country. Score is the hand-entered COUNTRIES profile.",
};

function renderBody(country: MapCountry, meanings?: Record<string, string> | null) {
  return render(<CountryTooltipBody country={country} meanings={meanings} />);
}

describe("the country tooltip", () => {
  it("shows no data for renewables and CO2 on a static profile, not the formula values", () => {
    const { container } = renderBody(ICELAND_STATIC);
    const text = container.textContent ?? "";

    // The control: the score itself is still shown.
    expect(text).toContain("Iceland");
    expect(text).toContain("86");

    expect(text, "both rows read no data").toMatch(/Renewables\s*no data/);
    expect(text).toMatch(/CO2 \/ capita\s*no data/);
    expect(text, "81.7 is 86 * 0.95 from the backend formula, not a measurement")
      .not.toContain("81.7");
    expect(text, "4.52 is 20 - 86 * 0.18 from the backend formula, not a measurement")
      .not.toContain("4.52");
  });

  it("shows no data for a live country's missing value, and not a number", () => {
    const { container } = renderBody(CHILE_LIVE_MISSING);
    const text = container.textContent ?? "";

    expect((text.match(/no data/g) ?? []).length).toBe(2);
    expect(text).not.toMatch(/NaN|null|undefined/);
  });

  it("shows a live country's measured values", () => {
    const { container } = renderBody(SWEDEN_LIVE);
    const text = container.textContent ?? "";

    expect(text).toContain("60.1%");
    expect(text).toContain("3.6 t");
    expect(text).not.toContain("no data");
  });

  it("does not treat a source-less country's numbers as measured", () => {
    const { container } = renderBody(UNLABELLED);
    const text = container.textContent ?? "";

    expect(text).toContain("source: unknown");
    expect(text, "an unlabelled value is not known to be measured").not.toContain("50%");
    expect((text.match(/no data/g) ?? []).length).toBe(2);
  });

  it("shows the backend's meaning in the source line when it sends one", () => {
    const hardcoded = renderBody(ICELAND_STATIC, MEANINGS).container.textContent ?? "";
    const benchmark = renderBody(STATIC_BENCHMARK_COUNTRY, MEANINGS).container.textContent ?? "";
    const live = renderBody(SWEDEN_LIVE, MEANINGS).container.textContent ?? "";

    expect(hardcoded).toContain("source: No external data for this country.");
    expect(benchmark).toContain("source: Live cache empty or stale.");
    expect(live).toContain("source: Score computed from a fresh live-cache entry");
    expect(hardcoded, "the raw token is replaced by the meaning").not.toContain("source: hardcoded");
  });

  it("falls back to the raw token when the response carries no meanings", () => {
    expect(renderBody(ICELAND_STATIC).container.textContent).toContain("source: hardcoded");
    expect(renderBody(STATIC_BENCHMARK_COUNTRY, null).container.textContent)
      .toContain("source: static_benchmark");
    expect(renderBody(SWEDEN_LIVE).container.textContent).toContain("source: world_bank_live");
  });

  it("falls back to the raw token for a token the meanings do not list", () => {
    const text = renderBody(STATIC_BENCHMARK_COUNTRY, { hardcoded: MEANINGS.hardcoded }).container.textContent ?? "";
    expect(text).toContain("source: static_benchmark");
  });

  it("styles both static kinds as static, and the live kind as live", () => {
    const sourceClass = (c: MapCountry, m: Record<string, string> | null) =>
      renderBody(c, m).container.querySelector(".tip-source");

    for (const meanings of [MEANINGS, null]) {
      expect(sourceClass(ICELAND_STATIC, meanings)?.classList.contains("tip-source-static")).toBe(true);
      expect(sourceClass(STATIC_BENCHMARK_COUNTRY, meanings)?.classList.contains("tip-source-static")).toBe(true);
      expect(sourceClass(SWEDEN_LIVE, meanings)?.classList.contains("tip-source-static")).toBe(false);
    }
  });

  it("shows no data for a static_benchmark country's physical values, as for hardcoded", () => {
    const text = renderBody(STATIC_BENCHMARK_COUNTRY, MEANINGS).container.textContent ?? "";

    expect((text.match(/no data/g) ?? []).length).toBe(2);
    expect(text, "73.2 is a benchmark figure, not a live measurement").not.toContain("73.2%");
    expect(text).not.toContain("6.1 t");
  });
});
