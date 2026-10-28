import { apiBlob } from "../client";
import { isMock } from "../mock";
import type { EvaluateProjectRequest } from "../types";

/** POST /api/v1/report/pdf renders a scored project. */
export type ReportRequest = EvaluateProjectRequest;
export const reportApi={
  pdf:async(body:ReportRequest):Promise<Blob>=>{
    if(isMock){const txt=`SORA.earth ESG Report (mock)\nProject: ${body.project_name||"-"}\nScore: 72.3 / 100\nGenerated: ${new Date().toISOString()}`;return new Blob([txt],{type:"application/pdf"});}
    // The shared transport: Bearer, session abort, typed errors. The route itself
    // has no optional_auth and no server-side no-store; this claims neither.
    return apiBlob("/report/pdf",{method:"POST",body:JSON.stringify(body),cache:"no-store"});}
};
