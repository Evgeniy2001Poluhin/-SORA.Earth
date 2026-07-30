import { api } from "../client";
import type { DiscrepancyResponse, UncertaintyResponse, ExplainLocalRequest } from "../types";
import { isMock } from "../mock";
const delay=<T,>(v:T,ms=300)=>new Promise<T>(r=>setTimeout(()=>r(v),ms));
const DISC:DiscrepancyResponse={
  models:{rf_v1:{proba:0.712,weight:0.34},stacking_v2:{proba:0.723,weight:0.33},calibrated_v2:{proba:0.720,weight:0.33,near_deterministic:false}},
  consensus:{weighted_proba:0.718,method:"weighted_average"},
  divergence:{max_spread:0.011,std:0.006,max_pair:["rf_v1","stacking_v2"]},
  tree_uncertainty:{std:0.031,ci_90:[0.672,0.774],n_trees:100},
  recommendation:"consensus"};
const UNC:UncertaintyResponse={
  probability:72.3,
  prediction:{mean:0.723,median:0.720,lower_90:0.672,upper_90:0.774},
  tree_distribution:{std:0.031,n_trees:100,min:0.610,max:0.845,p5:0.672,p95:0.774},
  confidence:"high",
  uncertainty:{method:"RF tree variance",mean:72.3,std:3.1,ci_90:[67.2,77.4],n_trees:100},
  reliability:"high"};
export const calibrationApi={
  discrepancy:(b:ExplainLocalRequest)=>isMock?delay(DISC):api<DiscrepancyResponse>("/calibration/discrepancy",{method:"POST",body:JSON.stringify(b)}),
  uncertainty:(b:ExplainLocalRequest)=>isMock?delay(UNC):api<UncertaintyResponse>("/predict/uncertainty",{method:"POST",body:JSON.stringify(b)}),
};
export interface CalibrationDataset{probs:number[];labels:number[];n_bins?:number}
export interface BrierResult{brier:number;ece:number;n_samples:number;n_bins:number}
export interface ReliabilityResult{n_samples:number;n_bins:number;base_rate:number;brier:number;ece:number;curve:{bin_lower:number[];bin_upper:number[];mean_predicted:(number|null)[];mean_observed:(number|null)[];count:number[]};murphy:{reliability:number;resolution:number;uncertainty:number}}
const mockBrier=(d:CalibrationDataset):BrierResult=>({brier:0.163,ece:0.153,n_samples:d.probs.length||80,n_bins:d.n_bins||10});
const mockReliability=(d:CalibrationDataset):ReliabilityResult=>({
  n_samples:d.probs.length||80,n_bins:10,base_rate:0.234,brier:0.163,ece:0.153,
  curve:{bin_lower:[0,.1,.2,.3,.4,.5,.6,.7,.8,.9],bin_upper:[.1,.2,.3,.4,.5,.6,.7,.8,.9,1],
    mean_predicted:[.05,.14,.24,.34,.44,.54,.64,.75,.88,.95],
    mean_observed:[.25,.14,.16,.57,null,.66,.74,.78,1,1],
    count:[6,8,10,9,0,11,12,8,9,7]},
  murphy:{reliability:0.032,resolution:0.099,uncertainty:0.234}});
export const calibrationQualityApi={
  brier:(d:CalibrationDataset)=>isMock?delay(mockBrier(d)):api<BrierResult>("/calibration/brier",{method:"POST",body:JSON.stringify(d)}),
  reliability:(d:CalibrationDataset)=>isMock?delay(mockReliability(d)):api<ReliabilityResult>("/calibration/reliability",{method:"POST",body:JSON.stringify(d)}),
};
