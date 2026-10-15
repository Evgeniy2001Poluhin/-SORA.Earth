import type { ServiceHealth } from "@/api/types";

/**
 * What the Evaluate hero and its printed report may say about the service,
 * read from GET /api/v1/health.
 *
 * The hero used to print "Operational", "RF · MLP · Stacking" and an AUC that
 * no code computed, whatever the service was doing. Each answer below is now
 * derived from the payload, and an answer the payload does not give is
 * "unknown". Nothing here falls back to a default that looks like a result.
 *
 * Callers pass the query state, not only its data. TanStack Query keeps the
 * last successful data after a failed refetch, so data alone says what the
 * service was, not what it is now.
 */
export type HealthStatus = "operational" | "degraded" | "unknown";

export interface HealthQueryState {
  data: ServiceHealth | undefined;
  /** True when the latest attempt failed, whatever `data` still holds. */
  isError: boolean;
}

export interface ServiceSnapshot {
  status: HealthStatus;
  /** Loaded models as printed: names joined with " · ", or a plain answer such as "unknown". */
  models: string;
}

/** The two answers the hero and the printed report both use. */
export function serviceSnapshotOf({ data, isError }: HealthQueryState): ServiceSnapshot {
  if (isError) return { status: "unknown", models: "unknown" };
  return { status: statusOf(data), models: modelsOf(data) };
}

/** Operational only for "healthy"; any other non-empty string is degraded; empty or blank is unknown. */
function statusOf(data: ServiceHealth | undefined): HealthStatus {
  const status: unknown = data?.status;
  if (typeof status !== "string" || status.trim() === "") return "unknown";
  return status === "healthy" ? "operational" : "degraded";
}

/** The three flags the payload may carry, each with the name of the model it stands for. */
const MODEL_FLAGS = [
  { key: "loaded", name: "RandomForest" },
  { key: "xgboost_loaded", name: "XGBoost" },
  { key: "neural_network_loaded", name: "MLP" },
] as const;

/**
 * Names the models whose flag is exactly true, and says what it does not know.
 *  - All three flags are booleans: the true ones, or "none loaded".
 *  - Some flag is missing or not a boolean: the true ones followed by
 *    " · others unknown", or "unknown" when no flag is true.
 *  - The models block is absent or not an object: "unknown".
 */
function modelsOf(data: ServiceHealth | undefined): string {
  const models: unknown = data?.checks?.models;
  if (!models || typeof models !== "object") return "unknown";
  const flags = models as Record<string, unknown>;
  const allBoolean = MODEL_FLAGS.every(({ key }) => typeof flags[key] === "boolean");
  const trueNames = MODEL_FLAGS.filter(({ key }) => flags[key] === true).map(({ name }) => name);
  if (allBoolean) return trueNames.length > 0 ? trueNames.join(" · ") : "none loaded";
  if (trueNames.length > 0) return `${trueNames.join(" · ")} · others unknown`;
  return "unknown";
}
