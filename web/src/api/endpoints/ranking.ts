import { api } from "@/api/client";
import type { RankingResponse } from "@/api/types";
export const rankingApi = {
  list: (limit = 30, offset = 0) =>
    api<RankingResponse>(`/analytics/country-ranking?limit=${limit}&offset=${offset}`),
};
