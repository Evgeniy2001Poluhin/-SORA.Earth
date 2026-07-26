import { api } from "../client";
import type { ExplainLocalRequest, ExplainLocalResponse } from "../types";
import { isMock } from "../mock";
const delay=<T,>(v:T,ms=300)=>new Promise<T>(r=>setTimeout(()=>r(v),ms));
const mockLocal=(b:ExplainLocalRequest):ExplainLocalResponse=>({
  prediction:72.3,base_value:50,
  top_contributions:[
    {feature:"co2_reduction",value:b.co2_reduction,shap_value:8.4},
    {feature:"budget",value:b.budget,shap_value:5.1},
    {feature:"social_impact",value:b.social_impact,shap_value:4.8},
    {feature:"duration_months",value:b.duration_months,shap_value:-2.3}],
});
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
