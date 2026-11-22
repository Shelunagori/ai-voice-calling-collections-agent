import type { Metadata, Viewport } from "next";
import { Inter_Tight, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import "./theme.css";
import { Nav } from "@/components/Nav";
import { Aurora } from "@/components/ui/Aurora";
import { SiteFooter } from "@/components/ui/SiteFooter";
import { THEME_BOOT } from "@/components/ui/theme-boot";
import ui from "@/components/ui/ui.module.css";

const display = Inter_Tight({ subsets: ["latin"], variable: "--font-display", display: "swap" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-mono", display: "swap" });

export const metadata: Metadata = {
  title: "AI Voice Collections Agent — portfolio POC",
  description:
    "Real-time collection conversations with deterministic policy controls. Synthetic data only; not a production collections service.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#05060a" },
    { media: "(prefers-color-scheme: light)", color: "#f4f5fa" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // Dark-first. THEME_BOOT runs in <head> before first paint and applies a saved light/dark choice, so the
    // attribute can differ from the server HTML: suppressHydrationWarning covers exactly that.
    <html lang="en" data-theme="dark" className={`${display.variable} ${mono.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT }} />
      </head>
      <body>
        <Aurora />
        <Nav />
        <div className={ui.page}>{children}</div>
        <SiteFooter />
      </body>
    </html>
  );
}
