import { useEffect, useState } from "react";
import { rankingApi } from "@/api/endpoints/ranking";
import type { RankingItem } from "@/api/types";
import "./ranking.css";

export function RankingPage() {
  const [rows, setRows] = useState<RankingItem[]>([]);
  const [loading, setLoading] = useState(true);
  useEffect(() => { rankingApi.list(50).then(r => setRows(r.data)).finally(() => setLoading(false)); }, []);
  return (
    <div className="rnk">
      <h1 className="rnk-title">Country ESG Ranking</h1>
      <p className="rnk-sub">Macro features for {rows.length || "—"} countries, sorted by ESG rank.</p>
      {loading ? <div className="rnk-loading">Loading…</div> : (
        <table className="rnk-table">
          <thead><tr><th>Rank</th><th>Country</th><th>HDI</th><th>GDP/cap</th><th>CO₂/cap</th><th>Renewable %</th><th>Gini</th><th>Gov Eff</th></tr></thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.country}>
          <td className="rnk-num">{r.esg_rank}</td>
                <td className="rnk-c">{r.country}</td>
                <td>{r.hdi.toFixed(3)}</td>
                <td>${r.gdp_per_capita.toLocaleString()}</td>
                <td>{r.co2_per_capita.toFixed(1)}</td>
                <td>{r.renewable_share.toFixed(1)}%</td>
                <td>{r.gini_index.toFixed(1)}</td>
                <td>{r.gov_effectiveness.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
