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
import { MemoryRouter } from "react-router-dom";

import RegionEsgCard, { type RegionEsgCardProps } from "./RegionEsgCard";

const BASE: RegionEsgCardProps = {
  score: 58.2,
  breakdown: { e_score: 60.0, s_score: 57.1, g_score: 55.4 },
  sourcesUsed: [],
  scoreKind: "structural",
  scoreVintage: "2024",
};

const textOf = (props: Partial<RegionEsgCardProps> = {}) =>
  render(
    <MemoryRouter>
      <RegionEsgCard {...BASE} {...props} />
    </MemoryRouter>
  ).container.textContent ?? "";

/** The value cell of the labelled row, so an assertion reads one value and
 *  not the whole text, where "2" would match almost anything. */
function rowValue(container: HTMLElement, label: string): string | null {
  const key = Array.from(container.querySelectorAll("span")).find(s => s.textContent === label);
  return key?.nextElementSibling?.textContent ?? null;
}

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

  it("counts the sources the API lists, with no denominator", () => {
    // F074. The old row read "2 из 3 ожидаемых": the 3 was the constant in the
    // backend's confidence formula, not a count of expected sources. The map's
    // expected set (REQUIRED_METRICS) is not in the API, so no "of M" is shown.
    const { container } = render(
      <MemoryRouter>
        <RegionEsgCard {...BASE} sourcesUsed={["sber_veb_baseline", "rosstat"]} />
      </MemoryRouter>,
    );
    const text = container.textContent ?? "";

    expect(text, "a constant denominator must not be shown").not.toContain("из 3");
    expect(text).not.toContain("67%");
    expect(rowValue(container, "Источников использовано")).toBe("2");
  });

  it("omits the source count when the API lists no sources", () => {
    // An empty list is no count. It is not inferred from `confidence`, which
    // the backend computes as min(1, n/3) and so is not a count once n >= 3.
    const { container } = render(
      <MemoryRouter>
        <RegionEsgCard {...BASE} sourcesUsed={[]} />
      </MemoryRouter>,
    );

    expect(container.textContent ?? "").not.toContain("Источников использовано");
  });

  it("renders a link to the detail page when code is provided", () => {
    const { container } = render(
      <MemoryRouter>
        <RegionEsgCard {...BASE} code="RU-MOW" />
      </MemoryRouter>
    );

    const link = container.querySelector('a[href="/region/RU-MOW"]');
    expect(link).toBeInTheDocument();
    expect(link?.textContent).toContain("Подробнее →");
  });

  it("omits the link when code is not provided", () => {
    const { container } = render(
      <MemoryRouter>
        <RegionEsgCard {...BASE} code={null} />
      </MemoryRouter>
    );

    const text = container.textContent ?? "";
    expect(text).not.toContain("Подробнее →");
    expect(container.querySelector('a[href^="/region/"]')).toBeNull();
  });
});
