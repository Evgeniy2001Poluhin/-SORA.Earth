import { useState } from "react";
import { motion } from "framer-motion";
import { toast } from "sonner";
import { explainApi } from "@/api/endpoints/explain";
import type { ExplainResponse } from "@/api/types";
import "./explain.css";

const defaults = { country: "Sweden", budget: 150000, co2_per_year: 120, social_score: 9, duration_months: 18 };

export function ExplainPage() {
  const [form, setForm] = useState<any>(defaults);
  const [r, setR] = useState<ExplainResponse | null>(null);
  const [loading, setLoading] = useState(false);

  async function run() {
    setLoading(true);
    try { setR(await explainApi.predict(form)); }
    catch (e:any) { toast.error(e?.message || "Failed"); }
    finally { setLoading(false); }
  }

  const max = r ? Math.max(...r.explanation.map(x => Math.abs(x.shap_value))) : 1;

  return (
    <div className="exp">
      <motion.h1 initial={{opacity:0,y:8}} animate={{opacity:1,y:0}} className="exp-title">SHAP Explainability</motion.h1>
      <p className="exp-sub">Why the model predicted this score — top features ranked by contribution.</p>
      <div className="exp-form">
        {(["country","budget","co2_per_year","social_score","duration_months"] as const).map(k => (
          <label key={k} className="exp-field">
            <span>{k.replace(/_/g," ").toUpperCase()}</span>
            <input value={form[k]} onChange={e => setForm({...form, [k]: k==="country" ? e.target.value : Number(e.target.value)})}/>
          </label>
        ))}
        <button className="exp-btn" onClick={run} disabled={loading}>{loading ? "Computing…" : "Explain"}</button>
      </div>
      {r && (
        <motion.div initial={{opacity:0}} animate={{opacity:1}} className="exp-result">
          <div className="exp-head">
            <div><span className="exp-k">Probability</span><span className="exp-v">{r.probability.toFixed(2)}%</span></div>
            <div><span className="exp-k">Base value</span><span className="exp-v">{r.base_value.toFixed(3)}</span></div>
            <div><span className="exp-k">Verdict</span><span className="exp-v">{r.prediction === 1 ? "POSITIVE" : "NEGATIVE"}</span></div>
          </div>
          <div className="exp-rows">
            {r.explanation.map((x, i) => {
              const w = Math.abs(x.shap_value) / max * 100;
              const pos = x.direction === "positive";
              return (
                <div key={i} className="exp-row">
                  <div className="exp-feat">{x.feature}</div>
                  <div className="exp-bar-wrap"><div className={"exp-bar " + (pos ? "pos" : "neg")} style={{width: `${w}%`}}/></div>
                  <div className={"exp-val " + (pos ? "pos" : "neg")}>{pos ? "+" : ""}{x.shap_value.toFixed(3)}</div>
                </div>
              );
            })}
          </div>
        </motion.div>
      )}
    </div>
  );
}
