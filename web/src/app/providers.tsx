import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import { Toaster } from "sonner";
import { useLayoutEffect, type ComponentType, type PropsWithChildren } from "react";
import { ThemeProvider } from "./ThemeProvider";
import { createAppQueryClient } from "./queryClient";
import { SessionBoundary } from "./SessionBoundary";
import { session } from "@/lib/session";

const appQueryClient = createAppQueryClient();
session.bindQueryClient(appQueryClient);

/**
 * `queryClient` and `router` exist for tests: the real QueryClient settings and
 * the real session boundary, with a fresh cache and a MemoryRouter.
 */
export function Providers({
  children,
  queryClient = appQueryClient,
  router: Router = BrowserRouter,
  initialEntries,
}: PropsWithChildren<{
  queryClient?: QueryClient;
  router?: ComponentType<PropsWithChildren<{ initialEntries?: string[] }>>;
  initialEntries?: string[];
}>) {
  // The session transition empties this client: the one the tree renders with.
  useLayoutEffect(() => {
    session.bindQueryClient(queryClient);
  }, [queryClient]);
  return (
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <Router initialEntries={initialEntries}>
          <SessionBoundary>{children}</SessionBoundary>
          <Toaster theme="system" position="bottom-right" richColors toastOptions={{ style: { background: "var(--bg-2)", color: "var(--text)", border: "1px solid var(--line)" } }}/>
        </Router>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
