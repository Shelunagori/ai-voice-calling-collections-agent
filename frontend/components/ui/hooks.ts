"use client";

import { useCallback, useSyncExternalStore } from "react";
import { THEME_KEY, type Theme } from "./theme-boot";

const readTheme = (): Theme => (document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark");

function subscribeTheme(cb: () => void) {
  const mo = new MutationObserver(cb);
  mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  return () => mo.disconnect();
}

/** Current theme (from <html data-theme>) and a toggle that persists the choice. Server snapshot is the dark default. */
export function useTheme(): [Theme, () => void] {
  const theme = useSyncExternalStore(subscribeTheme, readTheme, () => "dark" as Theme);
  const toggle = useCallback(() => {
    const next: Theme = readTheme() === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      /* storage unavailable: the theme still switches for this visit */
    }
  }, []);
  return [theme, toggle];
}

const RM = "(prefers-reduced-motion: reduce)";
function subscribeRM(cb: () => void) {
  const mq = window.matchMedia(RM);
  mq.addEventListener("change", cb);
  return () => mq.removeEventListener("change", cb);
}
/** prefers-reduced-motion, live. Server snapshot is false; CSS handles the pre-hydration case. */
export function usePrefersReducedMotion(): boolean {
  return useSyncExternalStore(subscribeRM, () => window.matchMedia(RM).matches, () => false);
}
