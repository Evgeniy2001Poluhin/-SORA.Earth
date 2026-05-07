import { ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Tooltip, ReferenceLine, ResponsiveContainer, Cell } from "recharts";
import type { ReliabilityResult } from "@/api/endpoints/calibration";

interface Props {
  data: ReliabilityResult | null;
}

export function ReliabilityChart({ data }: Props) {
  if (!data) {
    return <div className="rel-empty">Run analysis to see reliability diagram</div>;
  }
  const { curve } = data;
  const points = curve.bin_lower.map((_, i) => ({
    x: curve.mean_predicted[i],
    y: curve.mean_observed[i],
    count: curve.count[i],
  })).filter(p => p.x !== null && p.y !== null);

  const maxCount = Math.max(1, ...points.map(p => p.count));
  const colorFor = (c: number) => {
    const t = Math.min(1, c / maxCount);
    const r = Math.round(16 + (16 - 16) * t);
    const g = Math.round(185 + (235 - 185) * t);
    const b = Math.round(129 + (129 - 129) * t);
    return `rgb(${r},${g},${b})`;
  };

  return (
    <div className="rel-chart-wrap">
      <ResponsiveContainer width="100%" height={360}>
        <ScatterChart margin={{ top: 12, right: 24, bottom: 36, left: 36 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="x" type="number" domain={[0, 1]} tickCount={6}
                 label={{ value: "Predicted probability", position: "bottom", offset: 8 }} />
          <YAxis dataKey="y" type="number" domain={[0, 1]} tickCount={6}
                 label={{ value: "Observed frequency", angle: -90, position: "insideLeft" }} />
          <Tooltip formatter={(v) => (typeof v === "number" ? v.toFixed(3) : String(v))} />
          <ReferenceLine
            ifOverflow="extendDomain"
            stroke="#888"
            strokeDasharray="4 4"
            segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]}
            label={{ value: "Perfect", position: "right" }}
          />
          <Scatter data={points} line shape="circle">
            {points.map((p, i) => <Cell key={i} fill={colorFor(p.count)} />)}
          </Scatter>
        </ScatterChart>
      </ResponsiveContainer>
      <p className="rel-caption">Points near the diagonal indicate well-calibrated predictions. Dot color intensity reflects bin count.</p>
    </div>
  );
}
