import { useMemo } from "react";

type MCData = {
  /** Simulations that produced a score -- the meaning it always had. */
  n: number;
  /** Simulations asked for, after the endpoint's own clamping. Optional so a
   *  caller still on the pre-contract shape type-checks. */
  requested?: number;
  /** Simulations that raised. `requested === n + failed`. */
  failed?: number;
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
  /** The run failed outright -- every simulation raised, so the server
   *  answered 503. Distinct from "not run yet", which is what an absent
   *  `data` used to mean for both. */
  failed?: boolean;
  onRun: (n: number) => void;
};

export default function MonteCarlo(props: Props) {
  const { data: raw, loading, failed, onRun } = props;
  // #236, and the third crash path in EvaluatePage on the same trigger: this
  // renders inside it, on the Monte Carlo tab, so an empty 200 from
  // /evaluate/monte-carlo took the page down here too even after the result
  // and explain paths were guarded. `{}` is truthy, so `!data` passed it to
  // `data.histogram.counts` and to six `.toFixed` calls.
  //
  // A simulation is the histogram plus its percentiles; without them there is
  // nothing to draw, and the "Click Run to simulate" state is the honest one.
  const data =
    raw &&
    Array.isArray(raw.histogram?.counts) &&
    Array.isArray(raw.histogram?.edges) &&
    typeof raw.mean === "number"
      ? raw
      : undefined;
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
          {loading
            ? "Running simulations..."
            : failed
              ? "Simulation failed — no run produced a score."
              : "Click \"Run\" to simulate input uncertainty (+/- 15% triangular noise on budget, CO2, social impact)."}
        </div>
      ) : (
        <>
          <div className="mc-stats">
            <div>
              <span className="lbl">N</span>
              <span className="val tabular">
                {/* `n` is the count that produced a score. When some raised,
                    both numbers are shown: averaging 50 of 1000 samples is not
                    the same answer as averaging 50 of 50. */}
                {typeof data.failed === "number" && data.failed > 0
                  ? `${data.n} / ${data.requested}`
                  : data.n}
              </span>
            </div>
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
