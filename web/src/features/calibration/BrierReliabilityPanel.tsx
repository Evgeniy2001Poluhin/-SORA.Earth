import { useState } from "react";
import { calibrationQualityApi, type ReliabilityResult } from "@/api/endpoints/calibration";
import { ReliabilityChart } from "./ReliabilityChart";
import { toast } from "sonner";

// A small seeded generator (mulberry32). The panel's disclaimer calls its data
// a fixed illustrative dataset, so every click on a scenario must send the same
// arrays. With Math.random each click drew a new dataset, and the numbers moved
// on every press while the page said they were fixed.
const SYNTHETIC_SEED = 20261016;

function mulberry32(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function generateSynthetic(n = 80, miscalibration = 0.0) {
  const rand = mulberry32(SYNTHETIC_SEED);
  const probs: number[] = [];
  const labels: number[] = [];
  for (let i = 0; i < n; i++) {
    const p = rand();
    probs.push(p);
    const trueP = Math.max(0, Math.min(1, p + (rand() - 0.5) * 2 * miscalibration));
    labels.push(rand() < trueP ? 1 : 0);
  }
  return { probs, labels };
}

export function BrierReliabilityPanel() {
  const [data, setData] = useState<ReliabilityResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [scenario, setScenario] = useState<"perfect" | "moderate" | "biased">("perfect");

  async function run(scn: "perfect" | "moderate" | "biased") {
    setScenario(scn);
    setLoading(true);
    const mis = scn === "perfect" ? 0.0 : scn === "moderate" ? 0.15 : 0.35;
    const ds = generateSynthetic(80, mis);
    try {
      const r = await calibrationQualityApi.reliability({ ...ds, n_bins: 10 });
      setData(r);
      toast.success(`Synthetic demo: Brier=${r.brier.toFixed(3)}, ECE=${r.ece.toFixed(3)}`);
    } catch (e: unknown) {
      toast.error(`Failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="brier-panel">
      <header className="brier-head">
        <h2>Synthetic demonstration: how Brier and ECE work</h2>
        <p className="brier-demo-note">These numbers come from a fixed illustrative dataset, not from the deployed model's predictions or any real outcomes.</p>
        <p className="muted">Brier score · ECE · Murphy decomposition · Reliability diagram</p>
      </header>

      <div className="scn-row">
        <button onClick={() => run("perfect")} disabled={loading} className={scenario === "perfect" ? "active" : ""}>Perfect</button>
        <button onClick={() => run("moderate")} disabled={loading} className={scenario === "moderate" ? "active" : ""}>Moderate</button>
        <button onClick={() => run("biased")} disabled={loading} className={scenario === "biased" ? "active" : ""}>Biased</button>
      </div>

      {data && (
        <div className="kpi-row">
          <div className="kpi"><span>Brier</span><b>{data.brier.toFixed(3)}</b><em>lower=better</em></div>
          <div className="kpi"><span>ECE</span><b>{data.ece.toFixed(3)}</b><em>lower=better</em></div>
          <div className="kpi"><span>Reliability</span><b>{data.murphy.reliability.toFixed(3)}</b><em>Murphy</em></div>
          <div className="kpi"><span>Resolution</span><b>{data.murphy.resolution.toFixed(3)}</b><em>higher=better</em></div>
          <div className="kpi"><span>Uncertainty</span><b>{data.murphy.uncertainty.toFixed(3)}</b><em>base rate</em></div>
          <div className="kpi"><span>Samples</span><b>{data.n_samples}</b><em>{data.n_bins} bins</em></div>
        </div>
      )}

      <ReliabilityChart data={data} />
    </section>
  );
}
