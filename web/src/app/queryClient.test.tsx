/**
 * A 401 or 403 must not be retried, so the sign-in message appears immediately
 * rather than after three attempts and 7 seconds of loading skeleton.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("@/api/mock", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/mock")>();
  return { ...actual, isMock: false };
});

import HistoryPage from "@/features/history/HistoryPage";
import { isMock } from "@/api/mock";
import { stubStatus } from "@/test/http";
import { renderWithQuery } from "@/test/utils";
import { createAppQueryClient, shouldRetry } from "./queryClient";
import { ApiError } from "@/api/client";

const renderPage = () =>
  renderWithQuery(
    <MemoryRouter>
      <HistoryPage />
    </MemoryRouter>,
    { queryClient: createAppQueryClient() },
  );

describe("the guard on these tests", () => {
  it("really is in production mode", () => {
    expect(isMock).toBe(false);
  });
});

describe("the real path: 401 is not retried", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the sign-in message within 1500ms and requests exactly once", async () => {
    const stub = stubStatus(401);
    const { container } = renderPage();

    await waitFor(
      () => {
        expect(container.textContent).toContain("Sign in to view evaluation history");
      },
      { timeout: 1500 },
    );

    // The request was made exactly once (no retries)
    expect(stub).toHaveBeenCalledTimes(1);
  });
});

describe("shouldRetry predicate", () => {
  it("returns false for ApiError with status 401", () => {
    const error = new ApiError(401, "Unauthorized", {}, null);
    expect(shouldRetry(0, error)).toBe(false);
  });

  it("returns false for ApiError with status 403", () => {
    const error = new ApiError(403, "Forbidden", {}, null);
    expect(shouldRetry(0, error)).toBe(false);
  });

  it("returns true for ApiError with status 500 when failureCount is 0", () => {
    const error = new ApiError(500, "Internal Server Error", {}, null);
    expect(shouldRetry(0, error)).toBe(true);
  });

  it("returns true for ApiError with status 500 when failureCount is 2", () => {
    const error = new ApiError(500, "Internal Server Error", {}, null);
    expect(shouldRetry(2, error)).toBe(true);
  });

  it("returns false for ApiError with status 500 when failureCount is 3", () => {
    const error = new ApiError(500, "Internal Server Error", {}, null);
    expect(shouldRetry(3, error)).toBe(false);
  });

  it("returns true for a generic Error when failureCount is 0", () => {
    const error = new Error("network");
    expect(shouldRetry(0, error)).toBe(true);
  });
});
