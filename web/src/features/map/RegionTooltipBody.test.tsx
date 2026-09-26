/**
 * The map tooltip does not present the source count as a confidence percentage.
 *
 * `confidence` is `len(distinct source names) / 3` and the required metric set
 * spans exactly two sources — so it was 0.67 for all 85 regions on every input.
 * The card was fixed in #395 and the detail page in this PR, but the tooltip
 * still showed "· conf 67%" until this change.
 *
 * The tooltip is tested via the extracted `RegionTooltipBody` rather than
 * through `RussiaMap` because the map is react-leaflet: the tooltip renders as
 * a layer, not an element, and clicking a region means clicking that layer,
 * which left this content untestable inside the map component itself.
 */
import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";

import RegionTooltipBody from "./RegionTooltipBody";
import type { EnrichedRussianRegion } from "@/data/russia_regions";
import { RUSSIA_REGIONS } from "@/data/russia_regions";

describe("the region tooltip", () => {
  const region: EnrichedRussianRegion = {
    ...RUSSIA_REGIONS[0],
    esgScore: 58.2,
    esgBreakdown: { score: 58.2, e_score: 60.0, s_score: 57.1, g_score: 55.4 },
    confidence: 0.67,
  };

  it("does not show the source count as a confidence percentage", () => {
    const { container } = render(<RegionTooltipBody region={region} />);
    const text = container.textContent ?? "";

    expect(text, "0.67 is len(sources)/3 for every region; as '67%' it reads as certainty")
      .not.toContain("67%");
    expect(text).not.toMatch(/conf/i);
  });

  it("still shows the capital, the ESG score, and the breakdown", () => {
    // The control: the assertion above would also hold on a tooltip that
    // rendered nothing at all.
    const { container } = render(<RegionTooltipBody region={region} />);
    const text = container.textContent ?? "";

    expect(text).toContain(region.capital);
    expect(text).toContain("58.2");
    expect(text).toContain("E60");
    expect(text).toContain("S57");
    expect(text).toContain("G55");
  });
});
