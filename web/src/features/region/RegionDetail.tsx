import { useEffect, useState, type CSSProperties } from "react";
import { useParams, Link } from "react-router-dom";
import "./region-detail.css";
import { ScoreKindNote } from "@/features/map/ProvenanceNotes";
import { sourcesCountText } from "@/features/map/regionProvenance";

type Indicator = {
  source: string;
  metric: string;
  value: number | null;
  unit: string | null;
  observed_at: string | null;
  trend?: { date: string; value: number | null }[];
  points_count: number;
};

/** GET /api/v1/map/russia/{code} answers with a region, or an error envelope. */
type RegionDetailData = {
  error?: string;
  detail?: string;
  region: {
    code: string;
    name: string;
    capital?: string;
    district?: string;
    lat?: number;
    lon?: number;
    population?: number;
  };
  esg: { score: number; e_score: number; s_score: number; g_score: number } | null;
  confidence: number | null;
  sources_used: string[];
  sources_missing: string[];
  model_version: string | null;
  computed_at: string | null;
  score_kind?: string | null;
  score_vintage?: string | null;
  indicators: Indicator[];
  indicators_count: number;
  signals_total: number;
};

const GREEN = "#5A9A6F";
const YELLOW = "#C9A96E";
const RED = "#B85C5C";

function colorForScore(s: number): string {
  if (s >= 80) return GREEN;
  if (s >= 60) return "#8FB069";
  if (s >= 40) return YELLOW;
  if (s >= 20) return "#D08770";
  return RED;
}

export default function RegionDetail() {
  const { code = "" } = useParams();
  const [data, setData] = useState<RegionDetailData | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/v1/map/russia/" + code)
      .then(r => r.json())
      .then(d => setData(d))
      .catch(e => setErr(String(e)));
  }, [code]);

  if (err) return <div className="rd-page" style={{ color: RED }}>Error: {err}</div>;
  if (!data) return <div className="rd-page" style={{ color: "var(--muted)" }}>Loading {code}...</div>;
  if (data.error || data.detail) {
    return <div className="rd-page" style={{ color: "var(--muted)" }}>Region not found: {code}</div>;
  }

  const esg = data.esg;
  const kpis = esg ? [
    { label: "Total ESG", v: esg.score },
    { label: "Environmental", v: esg.e_score },
    { label: "Social", v: esg.s_score },
    { label: "Governance", v: esg.g_score },
  ] : [];

  return (
    <div className="rd-page">
      <Link to="/map" className="rd-back">← Back to map</Link>
      <h1 className="rd-title">
        {data.region.name}
        <span className="code">{data.region.code}</span>
      </h1>
      <div className="rd-meta">
        {data.region.capital && <span>Capital: <b>{data.region.capital}</b></span>}
        {data.region.district && <span>District: <b>{data.region.district}</b></span>}
        {data.region.population && <span>Population: <b>{data.region.population.toLocaleString("ru-RU")}</b></span>}
      </div>

      <ScoreKindNote scoreKind={data.score_kind} scoreVintage={data.score_vintage} />

      {esg && (
        <div className="rd-kpis">
          {kpis.map(k => (
            <div key={k.label} className="rd-card" style={{ "--score-color": colorForScore(k.v) } as CSSProperties}>
              <div className="label">{k.label}</div>
              <div className="value">{k.v.toFixed(1)}</div>
            </div>
          ))}
        </div>
      )}

      <div className="rd-row">
        <div className="rd-card">
          <div className="label">Источников</div>
          <div className="value" style={{ fontSize: 24 }}>
            {data.confidence != null ? sourcesCountText(data.confidence) : "-"}
          </div>
        </div>
        {(data.sources_used.length > 0 || data.sources_missing.length > 0) && (
          <div className="rd-card">
            <div className="label">Sources</div>
            <div className="rd-chips">
              {data.sources_used.map(s => <span key={s} className="rd-chip used">{s}</span>)}
              {data.sources_missing.map(s => <span key={s} className="rd-chip missing">{s}</span>)}
            </div>
          </div>
        )}
      </div>

      <h2 className="rd-section-title">Indicators<span className="count">({data.indicators_count})</span></h2>
      <div className="rd-table-wrap">
        <table className="rd-table">
          <thead>
            <tr>
              <th>Source</th>
              <th>Metric</th>
              <th className="num">Value</th>
              <th>Unit</th>
              <th>Observed</th>
              <th className="num">Points</th>
            </tr>
          </thead>
          <tbody>
            {data.indicators.map((ind, i) => (
              <tr key={i}>
                <td>{ind.source}</td>
                <td className="mono">{ind.metric}</td>
                <td className="num">{ind.value != null ? ind.value.toFixed(2) : "-"}</td>
                <td className="muted">{ind.unit || "-"}</td>
                <td className="muted">{ind.observed_at ? new Date(ind.observed_at).toLocaleDateString("ru-RU") : "-"}</td>
                <td className="num muted">{ind.points_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="rd-footer">model: {data.model_version || "-"} . computed at: {data.computed_at || "-"}</div>
    </div>
  );
}
