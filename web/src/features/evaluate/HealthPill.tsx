import type { HealthStatus } from "./serviceHealth";

// Only "operational" keeps the green pill. Degraded and unknown get their own
// classes (shell.css), so the pill never reads green without a healthy answer.
const HEALTH_PILL: Record<HealthStatus, { label: string; className: string }> = {
  operational: { label: "Operational", className: "status-pill" },
  degraded: { label: "Degraded", className: "status-pill is-degraded" },
  unknown: { label: "Unknown", className: "status-pill is-unknown" },
};

/** The service's health as the header and the Evaluate hero show it. */
export function HealthPill({ status }: { status: HealthStatus }) {
  const p = HEALTH_PILL[status];
  return <span className={p.className}>{p.label}</span>;
}
