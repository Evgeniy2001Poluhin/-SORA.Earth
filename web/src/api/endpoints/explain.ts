import { api } from "../client";
import type { ExplainLocalRequest, ExplainLocalResponse } from "../types";
import { isMock } from "../mock";
const delay=<T,>(v:T,ms=300)=>new Promise<T>(r=>setTimeout(()=>r(v),ms));
const mockLocal=(b:ExplainLocalRequest):ExplainLocalResponse=>({
  prediction:72.3,base_value:50,
  features:[
    {name:"co2_reduction",value:(b as any).co2_reduction_tons_per_year||120,shap:8.4},
    {name:"budget_usd",value:(b as any).budget_usd||150000,shap:5.1},
    {name:"social_impact",value:(b as any).social_impact_score||9,shap:4.8},
    {name:"duration_months",value:(b as any).project_duration_months||18,shap:-2.3},
    {name:"region",value:(b as any).country||"Sweden",shap:1.7},
    {name:"category",value:"solar",shap:4.6}],
} as any);
async function fetchWaterfallBlob(b:ExplainLocalRequest):Promise<Blob>{
  if(isMock){const svg=`<svg xmlns="http://www.w3.org/2000/svg" width="600" height="200"><rect width="600" height="200" fill="#F4ECDA"/><text x="20" y="30" font-family="sans-serif" fill="#28200E">SHAP Waterfall (mock)</text></svg>`;return new Blob([svg],{type:"image/svg+xml"});}
  const res=await fetch("/api/v1/predict/explain/waterfall",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)});
  if(!res.ok)throw new Error(`waterfall HTTP ${res.status}`);return res.blob();
}
export const explainApi={
  local:(b:ExplainLocalRequest)=>isMock?delay(mockLocal(b)):api<ExplainLocalResponse>("/explain/local",{method:"POST",body:JSON.stringify(b)}),
  waterfallBlob:fetchWaterfallBlob,
  globalUrl:(top_n=11,nsamples=100)=>`/api/v1/explain/global?top_n=${top_n}&nsamples=${nsamples}`,
};
