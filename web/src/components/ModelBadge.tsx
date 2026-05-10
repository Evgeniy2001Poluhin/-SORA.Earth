import { useModelVersion } from "../features/mlops/useModelVersion";
import "./ModelBadge.css";

export function ModelBadge() {
  const { data, isLoading, isError } = useModelVersion();
  if (isLoading) return <span className="model-badge model-badge--loading">model…</span>;
  if (isError || !data) return <span className="model-badge model-badge--error">model ?</span>;
  return (
    <span
      className={"model-badge model-badge--" + data.alias}
      title={data.name + " · v" + data.version + " · @" + data.alias}
    >
      <span className="model-badge__at">@</span>
      <span>{data.alias}</span>
      <span className="model-badge__dot">·</span>
      <span>v{data.version}</span>
    </span>
  );
}
