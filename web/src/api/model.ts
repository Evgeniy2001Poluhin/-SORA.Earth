export type ModelVersionInfo = { name: string; alias: string; version: string };

export async function fetchModelVersion(): Promise<ModelVersionInfo> {
  const r = await fetch("/api/v2/model/version");
  if (!r.ok) throw new Error("model/version " + r.status);
  return r.json();
}

export async function reloadModel(): Promise<{ status: string; version: string }> {
  const r = await fetch("/api/v2/model/reload", { method: "POST" });
  if (!r.ok) throw new Error("model/reload " + r.status);
  return r.json();
}
