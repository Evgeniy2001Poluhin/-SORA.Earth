import { api } from "../client";
import type { RetrainLogResponse, FeatureImportanceResponse } from "../types";
import { isMock } from "../mock";
const delay=<T,>(v:T,ms=250)=>new Promise<T>(r=>setTimeout(()=>r(v),ms));
const FI:FeatureImportanceResponse={features:[
  {name:"co2_reduction",importance:0.34},{name:"budget_usd",importance:0.22},
  {name:"social_impact",importance:0.18},{name:"duration_months",importance:0.12},
  {name:"region",importance:0.08},{name:"category",importance:0.06}]};
const RL:RetrainLogResponse={items:Array.from({length:6},(_,i)=>({
  id:2000+i,started_at:`2026-05-0${i+1}T08:00:00Z`,
  finished_at:`2026-05-0${i+1}T08:0${6+i}:20Z`,duration_sec:380+i*20,
  status:i<5?"success":"failed",trigger_source:"scheduled",job_name:"auto_retrain",
  model_version:`2.3.${i}`,data_version:null,message:null,error_message:null,
  metrics_json:JSON.stringify({f1_score:0.88+i*0.003,roc_auc:0.95+i*0.002})}))};
export const adminApi={
  retrainLog:()=>isMock?delay(RL):api<RetrainLogResponse>("/admin/retrain-log"),
  featureImportance:()=>isMock?delay(FI):api<FeatureImportanceResponse>("/model/feature-importance"),
};
