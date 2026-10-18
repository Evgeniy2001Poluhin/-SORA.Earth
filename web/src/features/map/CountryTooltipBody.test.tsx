/**
 * The country tooltip shows what each number is, where it came from, and when a
 * country has no score. C8: an incomplete country says so and names what is missing;
 * it never shows a number in place of the score.
 *
 * Tested through the extracted body: react-leaflet's Tooltip is a layer and is
 * not in the DOM until a marker is opened in a real map.
 */
import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";

import CountryTooltipBody from "./CountryTooltipBody";
import type { MapCountry } from "./countryData";

// As the backend sends a country with a complete, measured score.
const SWEDEN_OK: MapCountry = {
  code: "SE", name: "Sweden", lat: 60, lon: 15, esg: 81.5, band: "leader",
  esg_status: "ok", esg_source: "world_bank_live", missing_inputs: [],
  renewable_share_pct: 60.1, co2_intensity_t_per_capita: null,
};

// A complete score that uses one static reference value with a stated source.
const JAPAN_MIXED: MapCountry = {
  code: "JP", name: "Japan", lat: 36, lon: 138, esg: 77, band: "advanced",
  esg_status: "mixed", esg_source: "mixed", missing_inputs: [],
  renewable_share_pct: null, co2_intensity_t_per_capita: null,
};

// As the backend sends a country with a missing input: no score, names what is missing.
const ICELAND_INCOMPLETE: MapCountry = {
  code: "IS", name: "Iceland", lat: 65, lon: -19, esg: null, band: null,
  esg_status: "incomplete", esg_source: null,
  missing_inputs: ["gini_index", "gov_effectiveness"],
  renewable_share_pct: null, co2_intensity_t_per_capita: null,
};

// A real score of zero. It is a score, not a missing one.
const ZERO_OK: MapCountry = {
  code: "ZZ", name: "Zeroland", lat: 0, lon: 0, esg: 0, band: "lagging",
  esg_status: "ok", esg_source: "world_bank_live", missing_inputs: [],
  renewable_share_pct: null, co2_intensity_t_per_capita: null,
};

// As /map/countries sends esg_source_meanings.
const MEANINGS: Record<string, string> = {
  world_bank_live: "Every input measured live, in a fresh live-cache entry.",
  mixed: "Some inputs are static reference values with a stated source.",
};

function renderBody(country: MapCountry, meanings?: Record<string, string> | null) {
  return render(<CountryTooltipBody country={country} meanings={meanings} />);
}

describe("the country tooltip", () => {
  it("shows an incomplete country as incomplete, with what is missing, and no number", () => {
    const { container } = renderBody(ICELAND_INCOMPLETE, MEANINGS);
    const text = container.textContent ?? "";

    expect(text).toContain("Iceland");
    expect(text).toContain("incomplete");
    expect(text).toContain("no score");
    expect(text).toContain("Missing: Gini index, government effectiveness");
    // "CO2" carries a digit of its own; the check is for a score or a value, which would be digits.
    expect(text.replace("CO2", ""), "an incomplete country shows no score, no percentage").not.toMatch(/\d/);
    expect(text).not.toMatch(/NaN|null|undefined/);
  });

  it("gives no source line for an incomplete country", () => {
    const text = renderBody(ICELAND_INCOMPLETE, MEANINGS).container.textContent ?? "";
    expect(text).not.toContain("source:");
  });

  it("shows a real 0 score as 0, not as incomplete", () => {
    const { container } = renderBody(ZERO_OK, MEANINGS);
    const text = container.textContent ?? "";

    expect(text).toContain("Zeroland");
    expect(container.querySelector(".tip-score-num")?.textContent).toBe("0");
    expect(text).not.toContain("incomplete");
    expect(text).not.toContain("Missing");
  });

  it("labels a mixed score as mixed", () => {
    const text = renderBody(JAPAN_MIXED, MEANINGS).container.textContent ?? "";

    expect(text).toContain("77");
    expect(text).toContain("mixed: some inputs are static reference values");
  });

  it("does not label an ok score as mixed", () => {
    const text = renderBody(SWEDEN_OK, MEANINGS).container.textContent ?? "";
    expect(text).not.toContain("mixed");
  });

  it("shows a live country's measured renewables, and no data for a value it lacks", () => {
    const text = renderBody(SWEDEN_OK, MEANINGS).container.textContent ?? "";

    expect(text).toContain("60.1%");
    expect(text).toContain("no data");
    expect((text.match(/no data/g) ?? []).length).toBe(1);
  });

  it("shows the backend's meaning in the source line when it sends one", () => {
    const live = renderBody(SWEDEN_OK, MEANINGS).container.textContent ?? "";
    const mixed = renderBody(JAPAN_MIXED, MEANINGS).container.textContent ?? "";

    expect(live).toContain("source: Every input measured live");
    expect(mixed).toContain("source: Some inputs are static reference values");
  });

  it("falls back to the raw token when the response carries no meanings", () => {
    expect(renderBody(SWEDEN_OK).container.textContent).toContain("source: world_bank_live");
    expect(renderBody(JAPAN_MIXED, null).container.textContent).toContain("source: mixed");
  });

  it("names missing inputs by their human label", () => {
    const text = renderBody({ ...ICELAND_INCOMPLETE, missing_inputs: ["hdi"] }).container.textContent ?? "";
    expect(text).toContain("Missing: human development index");
  });
});
