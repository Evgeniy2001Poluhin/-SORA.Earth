import { api } from "../client";
import type { MlopsHealth, ModelStatus, ModelMetrics } from "../types";
import { isMock } from "../mock";
const delay = <T,>(v:T,ms=200)=>new Promise<T>(r=>setTimeout(()=>r(v),ms));
const M_HEALTH:any={status:"OK",mlflow:"connected",baseline:"fitted",model_version:"v2.3.1",uptime_hours:142.6};
const M_STATUS:any={model:"stacking_v2",version:"2.3.1",trained_at:"2026-04-28T10:00:00Z",samples:18420,features:7,calibrated:true};
const M_METRICS:any={accuracy:0.942,precision:0.918,recall:0.886,f1:0.902,auc:0.961,brier:0.063,ece:0.024};
export const mlopsApi={
  health:()=>isMock?delay(M_HEALTH):api<MlopsHealth>("/mlops/health"),
  modelStatus:()=>isMock?delay(M_STATUS):api<ModelStatus>("/model/status"),
  modelMetrics:()=>isMock?delay(M_METRICS):api<ModelMetrics>("/model/metrics"),
};
