// Shared by the root layout (server) and the client theme hook. No "use client": the layout inlines THEME_BOOT.
// The saved theme lives in a first-party cookie (not browser storage: client code must never use web storage,
// see tests/operator-proxy.test.ts). Only this inline script and the toggle read it; the server never does, so
// every page stays statically prerendered.
export type Theme = "dark" | "light";
export const THEME_KEY = "vca-theme";

/** Runs synchronously in <head> before first paint: applies a saved theme so it never flashes. */
export const THEME_BOOT = `(function(){try{var m=document.cookie.match(/(?:^|; )${THEME_KEY}=(light|dark)/);if(m)document.documentElement.setAttribute("data-theme",m[1])}catch(e){}})()`;

/** Persist the choice for a year, site-wide. */
export function saveTheme(t: Theme) {
  document.cookie = `${THEME_KEY}=${t}; path=/; max-age=31536000; SameSite=Lax`;
}
