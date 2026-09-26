"use client";

import { useEffect } from "react";

// Secondary safety net for the demo route. Voice/audio failures are handled inline by the
// session client; this only catches unexpected render errors so the page never falls back
// to the platform's generic "This page couldn't load" screen. No stack traces are shown.
export default function DemoError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error("[voice-demo] route error", error);
  }, [error]);
  return (
    <main className="wrap narrow stack">
      <h1 style={{ fontSize: 22 }}>The voice demo hit an unexpected error</h1>
      <p className="muted">
        The session was stopped. You can retry; if the microphone is the problem, choose <b>Typed input</b> before starting.
      </p>
      {error.digest && <p className="small muted mono">Reference: {error.digest}</p>}
      <div className="row">
        <button className="btn primary" onClick={() => reset()}>
          Retry
        </button>
        <a className="btn" href="/demo">
          Reload demo
        </a>
      </div>
    </main>
  );
}
