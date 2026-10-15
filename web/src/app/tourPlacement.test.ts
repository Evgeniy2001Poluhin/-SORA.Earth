import { describe, expect, it } from "vitest";
import { CARD_MARGIN, CARD_MAX_WIDTH, cardWidthFor, isOnScreen, resolvePlacement } from "./tourPlacement";

const PHONE = { width: 375, height: 812 };

// The Evaluate link at 375 x 812, as measured in the browser.
const EVALUATE_LINK = { left: 88, top: 85, width: 82, height: 40 };

describe("resolvePlacement", () => {
  it("keeps the step's own placement for a target on screen", () => {
    expect(resolvePlacement("bottom", EVALUATE_LINK, PHONE)).toBe("bottom");
  });

  it("keeps the placement for a target only partly on screen", () => {
    expect(resolvePlacement("bottom", { left: 340, top: 85, width: 82, height: 40 }, PHONE)).toBe("bottom");
  });

  it("centres a target off the right edge of a 375 px screen", () => {
    // Map link, measured at 375 x 812 inside the horizontally scrolled nav.
    expect(resolvePlacement("bottom", { left: 701, top: 85, width: 82, height: 40 }, PHONE)).toBe("center");
  });

  it("centres a target off the left edge of a 375 px screen", () => {
    expect(resolvePlacement("bottom", { left: -200, top: 85, width: 82, height: 40 }, PHONE)).toBe("center");
  });

  it("centres a target above or below the viewport", () => {
    expect(resolvePlacement("bottom", { left: 88, top: -300, width: 82, height: 40 }, PHONE)).toBe("center");
    expect(resolvePlacement("bottom", { left: 88, top: 900, width: 82, height: 40 }, PHONE)).toBe("center");
  });

  it("centres a target of zero width or zero height", () => {
    expect(resolvePlacement("bottom", { left: 88, top: 85, width: 0, height: 40 }, PHONE)).toBe("center");
    expect(resolvePlacement("bottom", { left: 88, top: 85, width: 82, height: 0 }, PHONE)).toBe("center");
  });

  it("centres a target that is not mounted", () => {
    expect(resolvePlacement("bottom", null, PHONE)).toBe("center");
  });

  it("leaves a step that already asks for center as center", () => {
    expect(resolvePlacement("center", EVALUATE_LINK, PHONE)).toBe("center");
  });
});

describe("isOnScreen", () => {
  it("counts a target that only touches the viewport edge as off screen", () => {
    expect(isOnScreen({ left: 375, top: 85, width: 82, height: 40 }, PHONE)).toBe(false);
    expect(isOnScreen({ left: -82, top: 85, width: 82, height: 40 }, PHONE)).toBe(false);
  });
});

describe("cardWidthFor", () => {
  it("fits the card inside a 375 px screen with CARD_MARGIN on each side", () => {
    const width = cardWidthFor(375);
    expect(width).toBe(351);
    // Centred: this is the card position the tour gets for an off-screen target.
    const left = (375 - width) / 2;
    expect(left).toBeGreaterThanOrEqual(CARD_MARGIN);
    expect(left + width).toBeLessThanOrEqual(375 - CARD_MARGIN);
  });

  it.each([320, 360, 375, 414, 600, 768, 1024, 1400])(
    "leaves at least CARD_MARGIN on each side at %i px",
    (viewport) => {
      const width = cardWidthFor(viewport);
      const left = (viewport - width) / 2;
      expect(left).toBeGreaterThanOrEqual(CARD_MARGIN);
      expect(left + width).toBeLessThanOrEqual(viewport - CARD_MARGIN);
    },
  );

  it("keeps the design width on wide screens", () => {
    expect(cardWidthFor(768)).toBe(CARD_MAX_WIDTH);
    expect(cardWidthFor(1400)).toBe(CARD_MAX_WIDTH);
  });

  it("is 296 px at 320 px", () => {
    expect(cardWidthFor(320)).toBe(296);
  });
});
