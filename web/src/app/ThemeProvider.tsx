import { useEffect, useState, type ReactNode } from "react";
import { ThemeCtx, type Theme } from "./theme-context";

export function ThemeProvider({ children }: { children: ReactNode }) {
  // Diploma lock: default to dark; ignore stale "light" in localStorage.
  const [theme, setTheme] = useState<Theme>("dark");

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("sora-theme", theme);
  }, [theme]);

  return (
    <ThemeCtx.Provider value={{ theme, toggle: () => setTheme(t => t === "dark" ? "light" : "dark") }}>
      {children}
    </ThemeCtx.Provider>
  );
}
