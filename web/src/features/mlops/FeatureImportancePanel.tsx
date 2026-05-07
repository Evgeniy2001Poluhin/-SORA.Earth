import { useQuery } from "@tanstack/react-query";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from "recharts";
import { adminApi } from "@/api/endpoints/admin";

const PALETTE = ["#2FE0A6", "#5BD6E0", "#7EA8E5", "#A78BFA", "#F5C84B", "#F59E5B", "#EF4444", "#9CA3AF", "#6B7280"];

export function FeatureImportancePanel() {
  const q = useQuery({ queryKey: ["feature-importance"], queryFn: adminApi.featureImportance });
  if (q.isLoading) return <div className="card-body"><p style={{ color: "var(--muted)" }}>Loading feature importance...</p></div>;
  if (q.isError) return <div className="card-body"><p style={{ color: "#EF4444" }}>Failed to load feature importance</p></div>;

  const list = q.data && q.data.features ? q.data.features : [];
  const data = list.slice().sort((a, b) => b.importance - a.importance);
  const max = data.length > 0 ? data[0].importance : 1;

  return (
    <div>
      <div className="eyebrow" style={{ marginBottom: 12 }}>Feature importance: {data.length} features</div>
      <div style={{ height: 280, background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.06)", borderRadius: 10, padding: 16 }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} layout="vertical" margin={{ left: 100, right: 16, top: 8, bottom: 8 }}>
            <XAxis type="number" domain={[0, max * 1.1]} stroke="var(--muted)" fontSize={11} />
            <YAxis type="category" dataKey="name" stroke="var(--muted)" fontSize={11} width={100} />
            <Tooltip cursor={{ fill: "rgba(255,255,255,0.04)" }} contentStyle={{ background: "var(--bg)", border: "1px solid rgba(255,255,255,0.1)", fontSize: 12 }} />
            <Bar dataKey="importance" radius={[0, 4, 4, 0]}>
              {data.map((_, i) => <Cell key={i} fill={PALETTE[i % PALETTE.length]} />)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}