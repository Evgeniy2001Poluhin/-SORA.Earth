import { useModelVersion } from "../features/mlops/useModelVersion";
import "./ModelBadge.css";

export function ModelBadge() {
  const { data, isLoading, isError, error } = useModelVersion();

  if (isLoading) {
    return <span className="model-badge model-badge--muted">model…</span>;
  }
  if (isError || !data) {
    const msg = error instanceof Error ? error.message : "model version unavailable";
    return (
      <span className="model-badge model-badge--muted" title={msg}>
        model: n/a
      </span>
    );
  }

  const alias = data.alias || "production";
  const version = data.version || "-";
  const short = version.slice(0, 8);
  const tip = data.name + " v" + version + " @" + alias;

  return (
    <span className={"model-badge model-badge--" + alias} title={tip}>
      <span className="model-badge__at">@</span>
      <span>{alias}</span>
      <span className="model-badge__dot">·</span>
      <span>v{short}</span>
    </span>
  );
}
