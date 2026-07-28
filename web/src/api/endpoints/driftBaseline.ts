import { api } from "../client";
import type { DriftBaselineStatus, DriftBaselineFitResponse, DriftSimulateResponse } from "../types";
import { isMock } from "../mock";
export type MlflowDriftEvent={run_id:string;start_time:string;experiment_id?:string;"metrics.drift_score"?:number;"metrics.drifted_features_count"?:number;"metrics.n_samples_ref"?:number;"metrics.n_samples_cur"?:number;"tags.baseline_id"?:string;"params.drifted_features"?:string;};
export type MlflowHistoryResp={events:MlflowDriftEvent[];count:number};
export type KsFeatureStat={ks_stat:number;p_value:number;drift:boolean};
export type ModelDriftKsResp={status:string;drift_detected:boolean;window:number;features:Record<string,KsFeatureStat>};
const delay=<T,>(v:T,ms=300)=>new Promise<T>(r=>setTimeout(()=>r(v),ms));
const STATUS:DriftBaselineStatus={exists:true,n_samples:734,feature_count:7,fitted_at:"2026-05-07T08:00:00Z"};
const KS:ModelDriftKsResp={status:"OK",drift_detected:true,window:200,features:{
  budget:{ks_stat:0.7616,p_value:0,drift:true},co2_reduction:{ks_stat:0.4768,p_value:0,drift:true},
  social_impact:{ks_stat:0.7439,p_value:0,drift:true},duration_months:{ks_stat:0.3842,p_value:0,drift:true}}};
const MLFLOW:MlflowHistoryResp={count:30,events:Array.from({length:30},(_,i)=>({
  run_id:"r"+Math.random().toString(16).slice(2,10),
  start_time:new Date(Date.now()-i*3600000).toISOString(),
  "metrics.drift_score":i===1?0.5:1,"metrics.drifted_features_count":4,
  "metrics.n_samples_ref":734,"metrics.n_samples_cur":200,
  "tags.baseline_id":"model_drift_endpoint","params.drifted_features":"budget,co2_reduction,social_impact,duration_months"}))};
export const driftBaselineApi={
  status:()=>isMock?delay(STATUS):api<DriftBaselineStatus>("/mlops/drift/baseline"),
  fit:(csv_path="data/projects.csv")=>isMock?delay<DriftBaselineFitResponse>({status:"fitted",n_samples:734,features:["budget","co2_reduction","social_impact","duration_months"]}):api<DriftBaselineFitResponse>(`/mlops/drift/baseline/fit?csv_path=${encodeURIComponent(csv_path)}`,{method:"POST"}),
  remove:()=>isMock?delay({status:"removed"}):api<{status:string}>("/mlops/drift/baseline",{method:"DELETE"}),
  simulate:(mode:"stable"|"drift"|"custom",n=50,shift?:number)=>{
    if(isMock)return delay<DriftSimulateResponse>({status:"simulated",mode,shift_sigma:shift??(mode==="drift"?2:0),shifts:{},observations:n});
    const q=new URLSearchParams({mode,n:String(n)});if(shift!==undefined)q.set("shift",String(shift));
    return api<DriftSimulateResponse>(`/mlops/drift/simulate?${q}`,{method:"POST"});
  },
  mlflowHistory:()=>isMock?delay(MLFLOW):api<MlflowHistoryResp>("/model/drift/mlflow-history"),
  ksReport:()=>isMock?delay(KS):api<ModelDriftKsResp>("/model/drift"),
};
