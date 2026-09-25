/**
 * The region card says what its score is, and does not dress a constant as a
 * confidence.
 *
 * Two things the card did, measured 2026-09-25:
 *
 * 1. It showed "ESG Score / Total 58.2" with no indication that the number is a
 *    structural index standing on static 2024 data. The backend declares that
 *    deliberately — `SCORE_KIND = "structural"` sits beside the formula, with a
 *    measurement next to it ("one distinct value per region against 99.9 for
 *    openmeteo temperature over the same window") — and the label lived only in
 *    the aggregator's own run result. It reached neither the model, the view,
 *    the map API, nor this card.
 *
 * 2. It showed "Confidence 67%" in green. That value is
 *    `len(distinct source names) / 3` and the required metric set spans exactly
 *    two sources, so it is 0.67 for every region on every input — measured over
 *    27 value combinations: 26 distinct total scores, one distinct confidence.
 *    A constant rendered as a percentage with a traffic light reads as "the
 *    model is two-thirds sure of this figure".
 *
 * These are the first tests for this screen: `RussiaMapModal.tsx` (284 lines),
 * `RussiaMap.tsx` (171) and `RegionDetail.tsx` (156) had none. The card is
 * asserted directly rather than through the modal because the map is
 * react-leaflet: selecting a region means clicking a layer, not an element, and
 * a stub that answered every URL with the API payload crashed the map with
 * "Invalid GeoJSON object" and rendered nothing — which looks exactly like the
 * card being absent.
 */
import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";

import RegionEsgCard, { type RegionEsgCardProps } from "./RegionEsgCard";

const BASE: RegionEsgCardProps = {
  score: 58.2,
  breakdown: { e_score: 60.0, s_score: 57.1, g_score: 55.4 },
  confidence: 0.67,
  sourcesUsed: [],
  scoreKind: "structural",
  scoreVintage: "2024",
};

const textOf = (props: Partial<RegionEsgCardProps> = {}) =>
  render(<RegionEsgCard {...BASE} {...props} />).container.textContent ?? "";

describe("the region ESG card", () => {
  it("still shows the score itself", () => {
    // The control: every assertion below would also hold on a card that
    // rendered nothing at all.
    const text = textOf();

    expect(text).toContain("ESG Score");
    expect(text).toContain("58.2");
  });

  it("says the score is a structural index and which data it stands on", () => {
    const text = textOf();

    expect(text).toContain("Структурный индекс");
    expect(text).toContain("2024");
  });

  it("carries the vintage it was given rather than a year of its own", () => {
    const text = textOf({ scoreVintage: "2027" });

    expect(text).toContain("2027");
    expect(text, "a year typed into the card would still read 2024").not.toContain("2024");
  });

  it("says nothing about a kind it was not given", () => {
    // A score from a path that does not declare its kind must not be captioned
    // as structural on this card's own initiative.
    const text = textOf({ scoreKind: null, scoreVintage: null });

    expect(text).not.toContain("Структурный индекс");
    expect(text, "the score is still shown").toContain("58.2");
  });

  it("marks a placeholder score as one", () => {
    // `/map/russia` serves two hardcoded regions when both database readers come
    // back empty; the API labels that in `source`, and the hook dropped the
    // label. Moscow showed 89.0 like any measured score.
    const text = textOf({ scoreKind: "mock", scoreVintage: null });

    expect(text).toContain("Заглушка");
    expect(text, "a mock must not be captioned as the structural index")
      .not.toContain("Структурный индекс");
  });

  it("does not call a real score a placeholder", () => {
    expect(textOf()).not.toContain("Заглушка");
  });

  it("does not present the source count as a confidence percentage", () => {
    const text = textOf();

    expect(text, "0.67 is len(sources)/3 for every region; as '67%' it reads as certainty")
      .not.toContain("67%");
    expect(text).toContain("Источников");
    expect(text).toContain("2 из 3");
  });

  it("omits the source count when there is none", () => {
    const text = textOf({ confidence: null });

    expect(text).not.toContain("Источников");
  });
});
