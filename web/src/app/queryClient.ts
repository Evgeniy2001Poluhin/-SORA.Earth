import { QueryClient } from "@tanstack/react-query";
import { ApiError } from "@/api/client";
import { isSessionEnded } from "@/lib/session";

/**
 * A 401 or 403 does not change on retry, so the sign-in message should appear
 * immediately rather than after three attempts and 7 seconds of loading skeleton.
 *
 * Nor is a request retried that was cancelled, or that answered after its
 * session ended: retrying it would send the previous account's request again
 * under the next account's session.
 */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
    return false;
  }
  if (isSessionEnded(error)) return false;
  return failureCount < 3;
}

export function createAppQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30000,
        refetchOnWindowFocus: false,
        retry: shouldRetry,
      },
      // A mutation is a write or a computation the user asked for once. A save
      // retried after a dropped connection could save twice.
      mutations: { retry: false },
    },
  });
}
