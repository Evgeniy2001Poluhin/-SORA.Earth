import { useEffect, useState, type CSSProperties } from "react";
import { useParams, Link } from "react-router-dom";
import "./region-detail.css";
import { ScoreKindNote, StaleNote } from "@/features/map/ProvenanceNotes";
import { sourcesCountText } from "@/features/map/regionProvenance";

type Input = {
  source: string;
  indicator: string;
  value: number | null;
  unit: string | null;
  temporal_kind: string | null;
  period_start: string | null;
  period_end: string | null;
  delivered_at: string | null;
  source_revision: string | null;
  missing: boolean;
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
  stale_since?: string | null;
  /** null when the server could not read the inputs -- not the same as none. */
  inputs: Input[] | null;
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

const INDICATOR_LABELS: Record<string, string> = {
  "esg_index_baseline": "Базовый ESG-индекс",
  "unemployment_rate": "Уровень безработицы",
  "avg_income_rub": "Средний доход",
  "life_expectancy": "Ожидаемая продолжительность жизни",
  "budget_transparency": "Прозрачность бюджета",
  "digital_gov_index": "Индекс цифрового госуправления",
};

const SOURCE_LABELS: Record<string, string> = {
  "sber_veb_baseline": "Сбер/ВЭБ, базовая линия",
  "rosstat": "Росстат",
};

const UNIT_LABELS: Record<string, string> = {
  "%": "%",
  "RUB": "₽",
  "years": "лет",
  "0-100": "из 100",
};

function formatValue(value: number, unit: string | null): string {
  const number = value.toLocaleString("ru-RU", { maximumFractionDigits: 2 });
  return unit ? `${number} ${UNIT_LABELS[unit] ?? unit}` : number;
}

function formatPeriod(input: Input): string {
  if (input.temporal_kind === "period" && input.period_start && input.period_end) {
    // The period bounds are midnight UTC: read the year in UTC, or a viewer
    // west of Greenwich sees 2024-01-01 as 2023.
    const start = new Date(input.period_start).getUTCFullYear();
    const end = new Date(input.period_end).getUTCFullYear();
    return start === end ? String(start) : `${start}-${end}`;
  }
  if (input.temporal_kind === "not_applicable") {
    return "без периода";
  }
  return "-";
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
      <StaleNote staleSince={data.stale_since} />

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

      <h2 className="rd-section-title">Входные данные оценки</h2>
      <div className="rd-table-wrap">
        <table className="rd-table">
          <thead>
            <tr>
              <th>Показатель</th>
              <th className="num">Значение</th>
              <th>Источник</th>
              <th>Период</th>
              <th>Получено</th>
            </tr>
          </thead>
          <tbody>
            {data.inputs == null && (
              <tr>
                <td colSpan={5} className="muted">Входные данные недоступны: сервер не смог их прочитать</td>
              </tr>
            )}
            {data.inputs && data.inputs.map((inp, i) => (
              <tr key={i}>
                <td>{INDICATOR_LABELS[inp.indicator] || inp.indicator}</td>
                <td className="num">
                  {inp.missing || inp.value == null
                    ? <span className="muted">нет данных</span>
                    : formatValue(inp.value, inp.unit)}
                </td>
                <td className="muted">{SOURCE_LABELS[inp.source] || inp.source}</td>
                <td className="muted">{formatPeriod(inp)}</td>
                <td className="muted">
                  {inp.delivered_at ? new Date(inp.delivered_at).toLocaleDateString("ru-RU") : "-"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="rd-footer">model: {data.model_version || "-"} . computed at: {data.computed_at || "-"}</div>
    </div>
  );
}
