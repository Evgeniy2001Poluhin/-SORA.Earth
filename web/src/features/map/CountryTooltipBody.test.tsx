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

function renderBody(country: MapCountry) {
  return render(<CountryTooltipBody country={country} />);
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

  it("names the source, and calls a static value a static reference value", () => {
    expect(renderBody(ICELAND_STATIC).container.textContent).toContain(
      "source: static reference value",
    );
    expect(renderBody(SWEDEN_LIVE).container.textContent).toContain("source: world_bank_live");
  });

  it("does not style a static reference value like a live one", () => {
    const staticEl = renderBody(ICELAND_STATIC).container.querySelector(".tip-source");
    const liveEl = renderBody(SWEDEN_LIVE).container.querySelector(".tip-source");

    expect(staticEl?.classList.contains("tip-source-static")).toBe(true);
    expect(liveEl?.classList.contains("tip-source-static")).toBe(false);
  });
});
