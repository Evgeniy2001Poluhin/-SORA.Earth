import { api } from "@/api/client";
import type { ExplainResponse } from "@/api/types";
export const explainApi = {
  predict: (body: any) =>
    api<ExplainResponse>("/predict/explain", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
};
