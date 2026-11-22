// Shared by the root layout (server) and the client theme hook. No "use client": the layout inlines THEME_BOOT.
export type Theme = "dark" | "light";
export const THEME_KEY = "vca-theme";

/** Runs synchronously in <head> before first paint: applies a saved theme so it never flashes. */
export const THEME_BOOT = `(function(){try{var t=localStorage.getItem("${THEME_KEY}");if(t==="light"||t==="dark")document.documentElement.setAttribute("data-theme",t)}catch(e){}})()`;
