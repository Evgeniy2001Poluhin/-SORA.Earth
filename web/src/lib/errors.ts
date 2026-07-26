/**
 * Extract a human-readable message from a caught value.
 *
 * Catch bindings and TanStack Query onError callbacks are typed as unknown, and
 * a rejected promise can carry anything. Narrow instead of asserting so a
 * non-Error rejection cannot produce "undefined" in a toast.
 */
export function errorMessage(error: unknown, fallback = "unknown"): string {
  if (error instanceof Error && error.message) return error.message;
  if (typeof error === "string" && error) return error;
  if (error && typeof error === "object" && "message" in error) {
    const { message } = error as { message: unknown };
    if (typeof message === "string" && message) return message;
  }
  return fallback;
}
