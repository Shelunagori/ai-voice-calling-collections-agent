"use client";

import { useEffect } from "react";
import { cx } from "@/components/ui/cx";
import ui from "@/components/ui/ui.module.css";

// Secondary safety net for the demo route. Voice/audio failures are handled inline by the
// session client; this only catches unexpected render errors so the page never falls back
// to the platform's generic "This page couldn't load" screen. No stack traces are shown.
export default function DemoError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error("[voice-demo] route error", error);
  }, [error]);
  return (
    <main style={{ maxWidth: 720, margin: "0 auto", padding: "72px 16px", display: "flex", flexDirection: "column", alignItems: "center", textAlign: "center", gap: 18 }}>
      <span className={ui.overline}>/voice demo</span>
      <h1 className={ui.h2}>
        The call <span className={ui.grad}>hit a snag.</span>
      </h1>
      <p className={ui.lead}>
        The session was stopped. You can retry; if the microphone is the problem, choose <b>Typed input</b> before starting.
      </p>
      {error.digest && <p className={cx(ui.fine, ui.mono)}>Reference: {error.digest}</p>}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 10, justifyContent: "center" }}>
        <button type="button" className={ui.cta} onClick={() => reset()}>
          <span className={ui.ctaGlow} aria-hidden="true" />
          Retry
        </button>
        <a className={ui.ghostBtn} href="/demo" style={{ minHeight: 52, padding: "0 24px" }}>
          Reload demo
        </a>
      </div>
    </main>
  );
}
