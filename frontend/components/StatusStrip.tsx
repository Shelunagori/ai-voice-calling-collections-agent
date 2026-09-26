"use client";

import { useEffect, useState } from "react";
import { API_BASE, Capabilities, getJSON } from "@/lib/api";

export function useCapabilities() {
  const [cap, setCap] = useState<Capabilities | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    getJSON<Capabilities>("/api/capabilities")
      .then(setCap)
      .catch((e: Error) => setError(e.message));
  }, []);
  return { cap, error };
}

function ProviderPill({ name, value }: { name: string; value: string }) {
  const mock = value.startsWith("mock") || value === "fake";
  return (
    <span className={`pill ${mock ? "muted" : "ok"}`} title={value}>
      {name}: {mock ? "mock" : value.split(":")[0]}
    </span>
  );
}

export function StatusStrip() {
  const { cap, error } = useCapabilities();
  if (error)
    return (
      <div className="banner">
        Backend not reachable at <code>{API_BASE}</code> ({error}). Start it locally or set NEXT_PUBLIC_API_BASE_URL.
      </div>
    );
  if (!cap) return <div className="muted small">Checking backend…</div>;
  return (
    <div className="row small">
      <span className="muted">Live backend v{cap.version}:</span>
      {Object.entries(cap.providers).map(([k, v]) => (
        <ProviderPill key={k} name={k} value={v} />
      ))}
      <span className={`pill ${cap.telephony.active ? "ok" : "muted"}`}>PSTN: {cap.telephony.active ? "active" : "disabled"}</span>
      <span className={`pill ${cap.browser_voice_available ? "ok" : "warn"}`}>
        browser voice: {cap.browser_voice_available ? "available" : "typed fallback"}
      </span>
    </div>
  );
}
