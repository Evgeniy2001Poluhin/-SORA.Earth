import { createContext, useContext } from "react";

export type Theme = "dark" | "light";
export type ThemeCtxValue = { theme: Theme; toggle: () => void };

/**
 * Kept out of ThemeProvider.tsx so that file only exports components:
 * mixing a hook in breaks Fast Refresh for the whole module.
 */
export const ThemeCtx = createContext<ThemeCtxValue>({ theme: "dark", toggle: () => {} });

export const useTheme = () => useContext(ThemeCtx);
