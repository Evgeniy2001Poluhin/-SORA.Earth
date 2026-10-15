import { useCallback, useEffect, useMemo, useState } from "react";
import TourTooltip from "./TourTooltip";
import Joyride, {
  EVENTS,
  STATUS,
  type CallBackProps,
  type FloaterProps,
  type Step,
} from "react-joyride";
import { CARD_MARGIN, resolvePlacement, type Rect } from "./tourPlacement";

const KEY = "sora_tour_done";

// disableBeacon: without it Joyride makes a step wait for a click on a beacon
// at its target. A continuous tour already skips the beacon after the first
// step, so this states that rule for every step rather than changing what a
// user sees.
const STEPS: Step[] = [
  {
    target: "body",
    placement: "center",
    disableBeacon: true,
    title: "Welcome to SORA.Earth",
    content: "AI platform for ESG project evaluation. Quick 60-second tour of the key features.",
  },
  {
    target: '[data-tour="evaluate"]',
    disableBeacon: true,
    title: "Evaluate projects",
    content: "Score any ESG project: success probability, CO2 impact, social and economic scores.",
  },
  {
    target: '[data-tour="copilot"]',
    disableBeacon: true,
    title: "AI Co-Pilot",
    content: "Ask questions in natural language — RAG-grounded answers with source citations.",
  },
  {
    target: '[data-tour="map"]',
    disableBeacon: true,
    title: "Geospatial map",
    content: "Explore ESG scores by country on an interactive map.",
  },
  {
    target: '[data-tour="status"]',
    disableBeacon: true,
    title: "System status",
    content: "Live service health for all components. You are all set.",
  },
];

const ST = {
  options: {
    primaryColor: "#15B887",
    backgroundColor: "#0d1714",
    arrowColor: "#0d1714",
    textColor: "#e6f0ec",
    overlayColor: "rgba(3, 10, 8, 0.72)",
    zIndex: 10000,
    width: 380,
  },
  tooltip: {
    borderRadius: 16,
    padding: 22,
    border: "1px solid rgba(47, 224, 166, 0.22)",
    boxShadow: "0 24px 60px rgba(0,0,0,0.55)",
  },
  tooltipTitle: { fontSize: 17, fontWeight: 700, marginBottom: 6 },
  tooltipContent: { fontSize: 14, lineHeight: 1.55, color: "#9fb4ac" },
  buttonNext: {
    background: "linear-gradient(180deg, #2FE0A6 0%, #15B887 100%)",
    color: "#062a20",
    fontWeight: 700,
    fontSize: 13.5,
    borderRadius: 10,
    padding: "9px 16px",
  },
  buttonBack: { color: "#9fb4ac", fontSize: 13.5, marginRight: 8 },
  buttonSkip: { color: "#6b7e77", fontSize: 13 },
  spotlight: { borderRadius: 10 },
};

// Popper keeps a card placed at its target at least CARD_MARGIN from the
// viewport edges. Its default is 5px, measured against the scroll parent.
const FLOATER_PROPS: Partial<FloaterProps> = {
  options: { preventOverflow: { boundariesElement: "viewport", padding: CARD_MARGIN } },
};

/**
 * For each step, whether its target cannot be seen. Reads the DOM, so it is
 * only meaningful once the nav is laid out: the tour calls it when a step is
 * about to open and on resize, never during render.
 */
function offScreenSteps(): boolean[] {
  const viewport = {
    width: document.documentElement.clientWidth,
    height: document.documentElement.clientHeight,
  };
  return STEPS.map((step) => {
    const el = typeof step.target === "string" ? document.querySelector(step.target) : step.target;
    let rect: Rect | null = null;
    if (el) {
      const r = el.getBoundingClientRect();
      rect = { left: r.left, top: r.top, width: r.width, height: r.height };
    }
    return resolvePlacement("bottom", rect, viewport) === "center";
  });
}

function focusNext() {
  document.querySelector<HTMLElement>('[data-action="primary"]')?.focus({ preventScroll: true });
}

export default function OnboardingTour({ runSignal = 0 }: { runSignal?: number }) {
  // Whether the tour was already completed is known before the first paint,
  // so read it as lazy initial state rather than setting it from an effect.
  const [run, setRun] = useState(() => {
    try { return !localStorage.getItem(KEY); } catch { return false; }
  });

  // A bumped runSignal re-opens the tour. Adjusting state during render avoids
  // committing a frame with the tour still closed.
  const [lastSignal, setLastSignal] = useState(runSignal);
  if (runSignal !== lastSignal) {
    setLastSignal(runSignal);
    if (runSignal > 0) setRun(true);
  }

  // Steps start on their own placement; measured before each step opens.
  const [offScreen, setOffScreen] = useState<boolean[]>(() => STEPS.map(() => false));
  const measure = useCallback(() => {
    const next = offScreenSteps();
    setOffScreen((prev) => (prev.every((v, i) => v === next[i]) ? prev : next));
  }, []);

  useEffect(() => {
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [measure]);

  const steps = useMemo(
    () => STEPS.map((step, i) => (offScreen[i] ? { ...step, placement: "center" as const } : step)),
    [offScreen],
  );

  const cb = (d: CallBackProps) => {
    // Joyride mounts a step's card with the placement it has at that moment,
    // so refresh before the next step can open.
    if (d.type === EVENTS.STEP_BEFORE) measure();
    // The card is hidden until Joyride opens it, and a focus call made while it
    // mounts does nothing. Focus Next once the card is open, without scrolling.
    // The welcome card is left alone, as Joyride left it.
    if (d.type === EVENTS.TOOLTIP && d.index > 0) window.setTimeout(focusNext, 0);
    if (([STATUS.FINISHED, STATUS.SKIPPED] as string[]).includes(d.status)) {
      localStorage.setItem(KEY, "1");
      setRun(false);
    }
  };

  return (
    <Joyride
      steps={steps}
      run={run}
      callback={cb}
      continuous
      showSkipButton
      showProgress
      // The tour must not scroll the page or the nav to reach a target. Joyride
      // would otherwise do both, and its focus handling goes with the scrolling;
      // focusNext and the Tab trap in TourTooltip cover focus instead.
      disableScrolling
      disableScrollParentFix
      floaterProps={FLOATER_PROPS}
      tooltipComponent={TourTooltip}
      styles={{ options: ST.options, spotlight: { borderRadius: 12 } }}
    />
  );
}
