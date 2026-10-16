import { api } from "../client";
import type { ServiceHealth } from "../types";
import { isMock } from "../mock";
const delay = <T,>(v:T,ms=200)=>new Promise<T>(r=>setTimeout(()=>r(v),ms));
// Honest about production: the RandomForest and XGBoost are loaded, and the
// neural network is not, because its weights are not shipped (see #320).
const M_SERVICE_HEALTH:ServiceHealth={status:"healthy",model_provenance:{source:"seed",model_version:"20260712_200332"},checks:{models:{status:"healthy",loaded:true,neural_network_loaded:false,xgboost_loaded:true}}};
export const healthApi={
  get:()=>isMock?delay(M_SERVICE_HEALTH):api<ServiceHealth>("/health"),
};
