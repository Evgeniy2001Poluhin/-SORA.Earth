import { useMemo } from "react";

type Row = {
  country: string;
  region?: string;
  total_score: number;
  environment_score: number;
  social_score: number;
  economic_score: number;
  risk_level: string;
};

type Props = {
  data: { count: number; ranking: Row[] } | undefined;
  loading: boolean;
  currentCountry: string;
  onPickCountry: (c: string) => void;
};

export default function CountryRanking(props: Props) {
  const { data, loading, currentCountry, onPickCountry } = props;
  const rows = data && data.ranking ? data.ranking : [];
  const maxScore = useMemo(() => {
    let m = 100;
    for (const r of rows) if (r.total_score > m) m = r.total_score;
    return m;
  }, [rows]);

  if (loading) {
    return <div className="cr-empty">Running 27 evaluations...</div>;
  }
  if (rows.length === 0) {
    return <div className="cr-empty">Run an evaluation first to see country ranking.</div>;
  }

  return (
    <div className="cr">
      <div className="cr-head">
        <span>COUNTRY</span>
        <span>SCORE</span>
        <span>RISK</span>
      </div>
      {rows.slice(0, 15).map((r, i) => {
        const pct = (r.total_score / maxScore) * 100;
        const me = r.country === currentCountry;
        const riskClass = "cr-risk " + (r.risk_level || "").toLowerCase();
        const title = "env " + r.environment_score + " soc " + r.social_score + " eco " + r.economic_score;
        return (
          <button
            key={r.country}
            className={"cr-row" + (me ? " me" : "")}
            onClick={() => onPickCountry(r.country)}
            title={title}
          >
            <span className="cr-rank">{i + 1}</span>
            <span className="cr-name">{r.country}</span>
            <span className="cr-track">
              <span className="cr-fill" style={{ width: pct + "%" }} />
            </span>
            <span className="cr-score">{r.total_score.toFixed(1)}</span>
            <span className={riskClass}>{r.risk_level}</span>
          </button>
        );
      })}
    </div>
  );
}
