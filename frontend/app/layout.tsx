import type { Metadata, Viewport } from "next";
import "./globals.css";
import { Nav } from "@/components/Nav";

export const metadata: Metadata = {
  title: "AI Voice Collections Agent — portfolio POC",
  description:
    "Real-time collection conversations with deterministic policy controls. Synthetic data only; not a production collections service.",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Nav />
        {children}
        <footer className="foot">
          Portfolio proof-of-concept · synthetic identities and accounts only · simulated demo policy, not legal advice ·
          not for contacting real debtors
        </footer>
      </body>
    </html>
  );
}
