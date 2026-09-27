import { QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import { Toaster } from "sonner";
import { PropsWithChildren } from "react";
import { ThemeProvider } from "./ThemeProvider";
import { createAppQueryClient } from "./queryClient";

const qc = createAppQueryClient();

export function Providers({ children }: PropsWithChildren) {
  return (
    <ThemeProvider>
      <QueryClientProvider client={qc}>
        <BrowserRouter>{children}<Toaster theme="system" position="bottom-right" richColors toastOptions={{ style: { background: "var(--bg-2)", color: "var(--text)", border: "1px solid var(--line)" } }}/></BrowserRouter>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
