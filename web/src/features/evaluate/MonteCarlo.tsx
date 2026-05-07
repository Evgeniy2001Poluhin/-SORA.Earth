import { useMemo } from "react";

type MCData = {
  n: number;
  mean: number;
  stdev: number;
  min: number;
  max: number;
  p10: number;
  p50: number;
  p90: number;
  histogram: { edges: number[]; counts: number[] };
};

type Props = {
  data: MCData | undefined;
  loading: boolean;
  onRun: (n: number) => void;
};

export default function MonteCarlo(props: Props) {
  const { data, loading, onRun } = props;
  const max = useMemo(() => {
    if (!data) return 1;
    let m = 1;
    for (const c of data.histogram.counts) if (c > m) m = c;
    return m;
  }, [data]);

  return (
    <div className="mc">
      <div className="mc-controls">
        <span className="eyebrow">SIMULATIONS</span>
        {[100, 500, 1000, 2000].map(n => (
          <button key={n} className="mc-btn" disabled={loading} onClick={() => onRun(n)}>
            {loading ? "..." : "Run " + n}
          </button>
        ))}
      </div>

      {!data ? (
        <div className="mc-empty">
          {loading ? "Running simulations..." : "Click \"Run\" to simulate input uncertainty (+/- 15% triangular noise on budget, CO2, social impact)."}
        </div>
      ) : (
        <>
          <div className="mc-stats">
            <div><span className="lbl">N</span><span className="val tabular">{data.n}</span></div>
            <div><span className="lbl">MEAN</span><span className="val tabular">{data.mean.toFixed(1)}</span></div>
            <div><span className="lbl">STDEV</span><span className="val tabular">{data.stdev.toFixed(2)}</span></div>
            <div className="hi"><span className="lbl">P10</span><span className="val tabular">{data.p10.toFixed(1)}</span></div>
            <div className="hi"><span className="lbl">P50</span><span className="val tabular">{data.p50.toFixed(1)}</span></div>
            <div className="hi"><span className="lbl">P90</span><span className="val tabular">{data.p90.toFixed(1)}</span></div>
          </div>

          <div className="mc-hist">
            {data.histogram.counts.map((c, i) => {
              const h = (c / max) * 100;
              const center = (data.histogram.edges[i] + data.histogram.edges[i+1]) / 2;
              const inP10P90 = center >= data.p10 && center <= data.p90;
              return (
                <div
                  key={i}
                  className={"mc-bar" + (inP10P90 ? " in" : "")}
                  style={{ height: h + "%" }}
                  title={"score " + center.toFixed(1) + "  count " + c}
                />
              );
            })}
          </div>

          <div className="mc-axis">
            <span>{data.min.toFixed(0)}</span>
            <span>{data.p50.toFixed(0)}</span>
            <span>{data.max.toFixed(0)}</span>
          </div>

          <div className="mc-legend">
            <span className="dot in"></span> P10-P90 confidence band
            <span className="sep">  </span>
            <span className="dot"></span> tails
          </div>
        </>
      )}
    </div>
  );
}
