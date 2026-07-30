import { isMock } from "../mock";
import type { EvaluateProjectRequest } from "../types";

/** POST /api/v1/report/pdf renders a scored project. */
export type ReportRequest = EvaluateProjectRequest;
export const reportApi={
  pdf:async(body:ReportRequest):Promise<Blob>=>{
    if(isMock){const txt=`SORA.earth ESG Report (mock)\nProject: ${body.project_name||"-"}\nScore: 72.3 / 100\nGenerated: ${new Date().toISOString()}`;return new Blob([txt],{type:"application/pdf"});}
    const res=await fetch("/api/v1/report/pdf",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    if(!res.ok)throw new Error("pdf failed");return res.blob();}
};
