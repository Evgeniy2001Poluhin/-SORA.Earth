import { useEffect, useState } from "react";

export interface RegionESG {
  score: number;
  e_score: number;
  s_score: number;
  g_score: number;
}

export interface Region {
  confidence?: number;
  sources_used?: string[];
  updated_at?: string | null;
  code: string;
  name: string;
  capital: string;
  district: string;
  lat: number;
  lon: number;
  population: number;
  esg: RegionESG;
}

interface ApiResp {
  regions: Region[];
  count: number;
  source: string;
}

export function useRussiaMap() {
  const [data, setData] = useState<Region[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    fetch("/api/v1/map/russia", { signal: ctrl.signal })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json() as Promise<ApiResp>;
      })
      .then((j) => setData(j.regions))
      .catch((e) => {
        if (e.name !== "AbortError") setError(String(e));
      })
      .finally(() => setLoading(false));
    return () => ctrl.abort();
  }, []);

  return { data, loading, error };
}
