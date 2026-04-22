import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import { Toaster } from "sonner";
import { PropsWithChildren } from "react";
const qc = new QueryClient({ defaultOptions:{ queries:{ staleTime:30000, refetchOnWindowFocus:false } } });
export function Providers({ children }: PropsWithChildren) {
  return (
    <QueryClientProvider client={qc}>
      <BrowserRouter>{children}<Toaster theme="dark" position="bottom-right" richColors/></BrowserRouter>
    </QueryClientProvider>
  );
}
