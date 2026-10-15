/**
 * Layout rules for the onboarding tour card. Pure functions with no DOM access:
 * OnboardingTour reads the rects and passes them in, and the tests call these
 * directly.
 */

/** The card's width on a wide screen: its design width. */
export const CARD_MAX_WIDTH = 412;

/** Least gap between the card and each edge of the viewport. */
export const CARD_MARGIN = 12;

export interface Rect {
  left: number;
  top: number;
  width: number;
  height: number;
}

export interface Viewport {
  width: number;
  height: number;
}

/**
 * The card width for a viewport: the design width, or the viewport less a
 * margin on each side, whichever is smaller.
 */
export function cardWidthFor(viewportWidth: number): number {
  return Math.max(0, Math.min(CARD_MAX_WIDTH, viewportWidth - 2 * CARD_MARGIN));
}

/** A target can be pointed at only if it has an area and overlaps the viewport. */
export function isOnScreen(target: Rect | null, viewport: Viewport): boolean {
  if (!target || target.width <= 0 || target.height <= 0) return false;
  return (
    target.left < viewport.width &&
    target.left + target.width > 0 &&
    target.top < viewport.height &&
    target.top + target.height > 0
  );
}

/**
 * The placement a step should get. A target that is off-screen or has no size
 * gets "center", so the card is centred in the viewport instead of placed at
 * coordinates the user cannot reach. Any other target keeps the placement the
 * step asked for.
 */
export function resolvePlacement<P extends string>(
  preferred: P,
  target: Rect | null,
  viewport: Viewport,
): P | "center" {
  return isOnScreen(target, viewport) ? preferred : "center";
}
