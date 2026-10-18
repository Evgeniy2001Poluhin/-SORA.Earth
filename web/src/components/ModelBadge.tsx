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

  // A value the server did not send is shown as "unknown", never as a default
  // alias or a placeholder version (audit F077).
  const alias = data.alias || "unknown";
  const hasVersion = Boolean(data.version);
  const versionText = hasVersion ? "v" + data.version.slice(0, 8) : "unknown";
  const tip =
    data.name + (hasVersion ? " v" + data.version : " version unknown") + " @" + alias;

  return (
    <span className={"model-badge model-badge--" + alias} title={tip}>
      <span className="model-badge__at">@</span>
      <span>{alias}</span>
      <span className="model-badge__dot">·</span>
      <span>{versionText}</span>
    </span>
  );
}
