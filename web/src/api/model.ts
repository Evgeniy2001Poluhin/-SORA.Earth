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
  const r = await fetch("/api/v1/model/status");
  if (!r.ok) throw new Error("model/status " + r.status);
  const j: StatusResponse = await r.json();
  const m = j.meta ?? {};
  return {
    name: m.algorithm ?? "model",
    alias: "production",
    version: m.retrained_at ?? "—",
    retrained_at: m.retrained_at,
    total_samples: m.total_samples,
    n_estimators: m.n_estimators,
    max_depth: m.max_depth,
    threshold: j.current_threshold,
  };
}

export async function reloadModel(): Promise<{ status: string; version: string }> {
  return { status: "noop", version: "n/a" };
}
