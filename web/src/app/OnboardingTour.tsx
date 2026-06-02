import { useState, useEffect } from "react";
import TourTooltip from "./TourTooltip";
import Joyride, { STATUS, type Step, type CallBackProps } from "react-joyride";

const KEY = "sora_tour_done";

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
    title: "Evaluate projects",
    content: "Score any ESG project: success probability, CO2 impact, social and economic scores.",
  },
  {
    target: '[data-tour="copilot"]',
    title: "AI Co-Pilot",
    content: "Ask questions in natural language — RAG-grounded answers with source citations.",
  },
  {
    target: '[data-tour="map"]',
    title: "Geospatial map",
    content: "Explore ESG scores across 32 countries on an interactive map.",
  },
  {
    target: '[data-tour="status"]',
    title: "System status",
    content: "Live service health with uptime over 24h and 7d. You are all set.",
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

export default function OnboardingTour({ runSignal = 0 }: { runSignal?: number }) {
  const [run, setRun] = useState(false);
  useEffect(() => {
    if (!localStorage.getItem(KEY)) setRun(true);
  }, []);
  useEffect(() => {
    if (runSignal > 0) setRun(true);
  }, [runSignal]);

  const cb = (d: CallBackProps) => {
    if (([STATUS.FINISHED, STATUS.SKIPPED] as string[]).includes(d.status)) {
      localStorage.setItem(KEY, "1");
      setRun(false);
    }
  };

  return (
    <Joyride
      steps={STEPS}
      run={run}
      callback={cb}
      continuous
      showSkipButton
      showProgress
      disableScrollParentFix
      tooltipComponent={TourTooltip}
      styles={{ options: ST.options, spotlight: { borderRadius: 12 } }}
    />
  );
}
