import { QueryClient } from "@tanstack/react-query";
import { ApiError } from "@/api/client";

/**
 * A 401 or 403 does not change on retry, so the sign-in message should appear
 * immediately rather than after three attempts and 7 seconds of loading skeleton.
 */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
    return false;
  }
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
    },
  });
}
