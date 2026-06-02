export type ModelVersionInfo = {
  name: string;
  alias: string;
  version: string;
  retrained_at?: string;
  total_samples?: number;
  n_estimators?: number;
  max_depth?: number;
  threshold?: number;
};

type StatusResponse = {
  current_threshold?: number;
  meta?: {
    retrained_at?: string;
    algorithm?: string;
    n_estimators?: number;
    max_depth?: number;
    features?: string[];
    total_samples?: number;
  };
};

export async function fetchModelVersion(): Promise<ModelVersionInfo> {
  // primary: lightweight v2 endpoint (always returns name/alias/version)
  const r = await fetch("/api/v2/model/version");
  if (!r.ok) throw new Error("model/version " + r.status);
  const j: { name: string; alias: string; version: string } = await r.json();
  // enrich with training details from v1 status (best-effort, non-fatal)
  let meta: StatusResponse["meta"] = {};
  let threshold: number | undefined;
  try {
    const sr = await fetch("/api/v1/model/status");
    if (sr.ok) {
      const sj: StatusResponse = await sr.json();
      meta = sj.meta ?? {};
      threshold = sj.current_threshold;
    }
  } catch { /* ignore — badge still works from v2 */ }
  return {
    name: j.name ?? meta.algorithm ?? "model",
    alias: j.alias ?? "production",
    version: j.version ?? meta.retrained_at ?? "—",
    retrained_at: meta.retrained_at,
    total_samples: meta.total_samples,
    n_estimators: meta.n_estimators,
    max_depth: meta.max_depth,
    threshold,
  };
}

export async function reloadModel(): Promise<{ status: string; version: string }> {
  return { status: "noop", version: "n/a" };
}
