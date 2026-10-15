import type { ServiceHealth } from "@/api/types";

/**
 * What the Evaluate hero may say about the service, read from GET /api/v1/health.
 *
 * The hero used to print "Operational", "RF · MLP · Stacking" and an AUC that
 * no code computed, whatever the service was doing. Each answer below is now
 * derived from the payload, and an answer the payload does not give is
 * "unknown". Nothing here falls back to a default that looks like a result.
 */
export type HealthStatus = "operational" | "degraded" | "unknown";

/** Operational only for "healthy"; any other string is degraded; no string is unknown. */
export function healthStatusOf(data: ServiceHealth | undefined): HealthStatus {
  const status: unknown = data?.status;
  if (typeof status !== "string") return "unknown";
  return status === "healthy" ? "operational" : "degraded";
}

/**
 * The models the service reports as loaded. RandomForest, XGBoost and MLP are
 * each named only when their own flag is true. If none of the flags is present
 * there is nothing to report, so the answer is "unknown".
 */
export function loadedModelsOf(data: ServiceHealth | undefined): string {
  const models: unknown = data?.checks?.models;
  if (!models || typeof models !== "object") return "unknown";
  const flags = models as { loaded?: unknown; xgboost_loaded?: unknown; neural_network_loaded?: unknown };
  const reported = [flags.loaded, flags.xgboost_loaded, flags.neural_network_loaded]
    .some((v) => typeof v === "boolean");
  if (!reported) return "unknown";
  const names: string[] = [];
  if (flags.loaded === true) names.push("RandomForest");
  if (flags.xgboost_loaded === true) names.push("XGBoost");
  if (flags.neural_network_loaded === true) names.push("MLP");
  return names.length > 0 ? names.join(" · ") : "none loaded";
}
