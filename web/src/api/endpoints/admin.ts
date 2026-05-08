import { api } from "../client";
import type { RetrainLogResponse, FeatureImportanceResponse } from "../types";
import { isMock } from "../mock";
const delay=<T,>(v:T,ms=250)=>new Promise<T>(r=>setTimeout(()=>r(v),ms));
const FI:any={features:[
  {name:"co2_reduction",importance:0.34},{name:"budget_usd",importance:0.22},
  {name:"social_impact",importance:0.18},{name:"duration_months",importance:0.12},
  {name:"region",importance:0.08},{name:"category",importance:0.06}]};
const RL:any={runs:Array.from({length:6},(_,i)=>({
  run_id:"r"+(2000+i),started_at:`2026-05-0${i+1}T08:00:00Z`,
  status:i<5?"success":"running",duration_s:380+i*20,
  metrics:{f1:0.88+i*0.003,auc:0.95+i*0.002}}))};
export const adminApi={
  retrainLog:()=>isMock?delay(RL):api<RetrainLogResponse>("/admin/retrain-log"),
  featureImportance:()=>isMock?delay(FI):api<FeatureImportanceResponse>("/model/feature-importance"),
};
