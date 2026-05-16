import { api } from "../client";
import type { MlopsHealth, ModelStatus, ModelMetrics } from "../types";
import { isMock } from "../mock";
const delay = <T,>(v:T,ms=200)=>new Promise<T>(r=>setTimeout(()=>r(v),ms));
const M_HEALTH:any={model_status:"healthy",drift_status:"stable",observations_tracked:147,monitoring:{prometheus:"/metrics",mlflow:"/mlflow/stats",drift:"/mlops/drift"}};
const M_STATUS:any={model:"stacking_v2",version:"2.3.1",trained_at:"2026-04-28T10:00:00Z",samples:18420,features:7,calibrated:true};
const M_METRICS:any={metrics:{accuracy:0.942,f1_score:0.902,roc_auc:0.961,best_f1:0.915,best_threshold:0.37,train_samples:589,test_samples:148},meta:{retrained_at:"20260507_151132",algorithm:"RandomForestClassifier"},models_available:[]};
export const mlopsApi={
  health:()=>isMock?delay(M_HEALTH):api<MlopsHealth>("/mlops/health"),
  modelStatus:()=>isMock?delay(M_STATUS):api<ModelStatus>("/model/status"),
  modelMetrics:()=>isMock?delay(M_METRICS):api<ModelMetrics>("/model/metrics"),
};
