import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { api } from "@/api/client";
import "./compliance.css";

type CategoryResult = {
  score: number;
  status: "ready" | "partial" | "gap";
  gaps: string[];
};

type CSRDResult = {
  framework: string;
  framework_version: string;
  project_name: string;
  overall_readiness: number;
  status: string;
  audit_ready: boolean;
  categories: Record<string, CategoryResult>;
  missing_evidence: string[];
  recommended_actions: {
    category: string;
    priority: string;
    action: string;
    current_score: number;
  }[];
};

const PRESETS = [
  {
    id: "solar",
    label: "Solar Farm",
    body: {
      project_name: "Solar Farm France",
      country: "France",
      budget_usd: 250000,
      co2_reduction_tons_per_year: 400,
      social_impact_score: 8,
      project_duration_months: 24,
      category: "Solar Energy",
    },
  },
  {
    id: "refo",
    label: "Reforestation",
    body: {
      project_name: "Boreal Reforestation",
      country: "Canada",
      budget_usd: 180000,
      co2_reduction_tons_per_year: 320,
      social_impact_score: 9,
      project_duration_months: 36,
      category: "Reforestation",
    },
  },
  {
    id: "water",
    label: "Water Treatment",
    body: {
      project_name: "Urban Water Grid",
      country: "Spain",
      budget_usd: 120000,
      co2_reduction_tons_per_year: 180,
      social_impact_score: 9,
      project_duration_months: 20,
      category: "Water Treatment",
    },
  },
  {
    id: "tiny",
    label: "Low-quality (gap demo)",
    body: {
      project_name: "Small Pilot",
      country: "Germany",
      budget_usd: 5000,
      co2_reduction_tons_per_year: 10,
      social_impact_score: 3,
      project_duration_months: 4,
      category: "Other",
    },
  },
];

const CATEGORY_LABELS: Record<string, string> = {
  E1_Climate: "E1 · Climate Change",
  E3_Wate: "E3 · Water & Marine",
  E4_Biodiversity: "E4 · Biodiversity",
  S1_Workforce: "S1 · Own Workforce",
  G1_Governance: "G1 · Business Conduct",
};

export function CompliancePage() {
  const [form, setForm] = useState(PRESETS[0].body);
  const [active, setActive] = useState("solar");

  const mut = useMutation({
    mutationFn: (body: typeof form) =>
      api<CSRDResult>("/compliance/csrd", {
        method: "POST",
        body: JSON.stringify({ ...body, name: body.project_name }),
      }),
  });

  const update = (k: keyof typeof form, v: any) =>
    setForm((f) => ({ ...f, [k]: v }));

  const applyPreset = (id: string) => {
    const p = PRESETS.find((x) => x.id === id);
    if (p) {
      setForm(p.body);
      setActive(id);
    }
  };

  return (
    <div className="compliance-page">
      <header className="cmp-header">
        <h1>CSRD / ESRS Compliance</h1>
        <p className="muted">
          European Sustainability Reporting Standards readiness · Post-Omnibus I
          (2026) · 5 Categories · Audit-ready gap analysis
        </p>
      </header>

      <section className="cmp-presets">
        {PRESETS.map((p) => (
          <button
            key={p.id}
            className={`preset ${active === p.id ? "on" : ""}`}
            onClick={() => applyPreset(p.id)}
          >
            {p.label}
          </button>
        ))}
      </section>

      <section className="cmp-form">
        <div className="row">
          <label>
            Project name
            <input
              value={form.project_name}
              onChange={(e) => update("project_name", e.target.value)}
            />
          </label>
          <label>
            Country
            <input
              value={form.country}
              onChange={(e) => update("country", e.target.value)}
            />
          </label>
          <label>
            Category
            <input
              value={form.category}
              onChange={(e) => update("category", e.target.value)}
            />
         </label>
        </div>
        <div className="row">
          <label>
            Budget (USD)
            <input
              type="number"
              value={form.budget_usd}
              onChange={(e) => update("budget_usd", Number(e.target.value) || 0)}
            />
          </label>
          <label>
            CO₂ reduction (t/y)
            <input
              type="number"
              value={form.co2_reduction_tons_per_year}
              onChange={(e) =>
                update("co2_reduction_tons_per_year", Number(e.target.value) || 0)
              }
            />
          </label>
          <label>
            Social impact (1–10)
            <input
              type="number"
              min={1}
              max={10}
              value={form.social_impact_score}
              onChange={(e) => update("social_impact_score", Number(e.target.value) || 0)}
            />
          </label>
          <label>
            Duration (months)
            <input
              type="number"
              value={form.project_duration_months}
              onChange={(e) =>
                update("project_duration_months", Number(e.target.value) || 0)
              }
            />
          </label>
        </div>
        <button
          className="primary"
          disabled={mut.isPending}
          onClick={() => mut.mutate(form)}
        >
          {mut.isPending ? "Assessing…" : "Run CSRD/ESRS check"}
        </button>
      </section>

      {mut.data && (
        <motion.section
          className="cmp-result"
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          key={mut.data.project_name + mut.data.overall_readiness}
        >
          <div className="overall">
            <div className={`big-score s-${mut.data.status}`}>
              {mut.data.overall_readiness.toFixed(0)}
              <span>/100</span>
            </div>
            <div className="meta">
              <div>
                <b>Framework:</b> {mut.data.framework_version}
              </div>
              <div>
              <b>Status:</b> {mut.data.status}
              </div>
              <div>
                <b>Audit-ready:</b>{" "}
                {mut.data.audit_ready ? "✓ yes" : "— no"}
              </div>
              <div>
                <b>Project:</b> {mut.data.project_name}
              </div>
            </div>
          </div>

          <h3>ESRS categories</h3>
          <div className="cats">
            {Object.entries(mut.data.categories).map(([k, v]) => (
              <div key={k} className={`cat s-${v.status}`}>
                <div className="cat-head">
                  <span>{CATEGORY_LABELS[k] || k}</span>
                  <b>{v.score.toFixed(0)}</b>
                </div>
                <div className="bar">
                  <div className="fill" style={{ width: `${v.score}%` }} />
                </div>
                {v.gaps.length > 0 && (
                  <ul className="gaps">
                    {v.gaps.map((g, i) => (
                      <li key={i}>{g}</li>
                ))}
                  </ul>
                )}
              </div>
            ))}
          </div>

          {mut.data.recommended_actions.length > 0 && (
            <>
              <h3>Recommended actions</h3>
              <ol className="actions">
                {mut.data.recommended_actions.map((a, i) => (
                  <li key={i} className={`prio-${a.priority}`}>
                    <span className="prio-tag">{a.priority}</span>
                    <b>[{a.category}]</b> {a.action}
                  </li>
                ))}
              </ol>
            </>
          )}
        </motion.section>
      )}

      {mut.error && (
        <div className="err">Error: {(mut.error as Error).message}</div>
      )}
    </div>
  );
}

export default CompliancePage;
