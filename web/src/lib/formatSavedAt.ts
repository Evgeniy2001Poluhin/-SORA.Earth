export function formatSavedAt(row: { saved_at_utc?: string | null; created_at: string | null }): string {
  if (row.saved_at_utc) {
    return new Date(row.saved_at_utc).toLocaleString("en-GB", {
      timeZone: "UTC", timeZoneName: "short",
      year: "numeric", month: "short", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    });
  }
  return row.created_at ? row.created_at.replace("T", " ") + " (timezone unknown)" : "—";
}
