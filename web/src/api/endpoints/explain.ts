import { api } from "../client";
import type { ExplainLocalRequest, ExplainLocalResponse } from "../types";

async function fetchWaterfallBlob(b: ExplainLocalRequest): Promise<Blob> {
  const res = await fetch("/api/v1/predict/explain/waterfall", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(b),
  });
  if (!res.ok) throw new Error("waterfall HTTP " + res.status);
  return res.blob();
}

export const explainApi = {
  local: (b: ExplainLocalRequest) =>
    api<ExplainLocalResponse>("/explain/local", {
      method: "POST",
      body: JSON.stringify(b),
    }),
  waterfallBlob: fetchWaterfallBlob,
  globalUrl: (top_n: number = 11, nsamples: number = 100) =>
    "/api/v1/explain/global?top_n=" + top_n + "&nsamples=" + nsamples,
};
