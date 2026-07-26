import { api } from "../client";
import type { MlopsHealth, ModelStatus, ModelMetrics, ModelMeta, RetrainMetrics } from "../types";
import { isMock } from "../mock";
const delay = <T,>(v:T,ms=200)=>new Promise<T>(r=>setTimeout(()=>r(v),ms));
const M_META:ModelMeta={retrained_at:"20260507_151132",algorithm:"RandomForestClassifier",n_estimators:200,max_depth:12,features:["budget","co2_reduction","social_impact","duration_months","budget_per_month","co2_per_dollar","efficiency_score"],total_samples:18420};
const M_RETRAIN_METRICS:RetrainMetrics={accuracy:0.942,f1_score:0.902,best_f1:0.915,roc_auc:0.961,best_threshold:0.37,train_samples:589,test_samples:148};
const M_HEALTH:MlopsHealth={model_status:"healthy",drift_status:"stable",observations_tracked:147,monitoring:{prometheus:"/metrics",mlflow:"/mlflow/stats",drift:"/mlops/drift"}};
const M_STATUS:ModelStatus={current_threshold:0.37,meta:M_META,retrain_history:[
  {status:"success",trigger_source:"scheduled",started_at:"2026-05-07T15:11:32Z",finished_at:"2026-05-07T15:17:52Z",duration_sec:380,model_version:"2.3.1",metrics:M_RETRAIN_METRICS},
]};
const M_METRICS:ModelMetrics={metrics:M_RETRAIN_METRICS,meta:M_META,models_available:[]};
export const mlopsApi={
  health:()=>isMock?delay(M_HEALTH):api<MlopsHealth>("/mlops/health"),
  modelStatus:()=>isMock?delay(M_STATUS):api<ModelStatus>("/model/status"),
  modelMetrics:()=>isMock?delay(M_METRICS):api<ModelMetrics>("/model/metrics"),
};
