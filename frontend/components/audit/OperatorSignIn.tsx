"use client";

import { useState } from "react";
import { operatorSignIn } from "@/lib/session-detail";

export function OperatorSignIn({ onSignedIn }: { onSignedIn: () => void }) {
  const [password, setPassword] = useState(""); // component state only; never persisted
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    const r = await operatorSignIn(password);
    setBusy(false);
    setPassword("");
    if (r.ok) onSignedIn();
    else setMsg(r.message ?? "Sign-in failed.");
  }
  return (
    <form className="panel stack" onSubmit={submit} aria-label="Operator sign-in" style={{ maxWidth: 420 }}>
      <h3>Operator sign-in</h3>
      <input type="password" placeholder="Operator console password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
      <button className="btn primary" disabled={busy || !password}>
        {busy ? "Signing in…" : "Sign in"}
      </button>
      {msg && (
        <div className="banner" role="alert">
          {msg}
        </div>
      )}
      <p className="muted small">Creates an HttpOnly session cookie for this site (8 h). The backend operator token stays on the server.</p>
    </form>
  );
}
