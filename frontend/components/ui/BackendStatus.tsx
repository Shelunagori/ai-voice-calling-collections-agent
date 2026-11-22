"use client";

import { m } from "framer-motion";
import { useCapabilities } from "@/components/StatusStrip";
import { API_BASE } from "@/lib/api";
import { retryWake } from "@/lib/backend-wake";
import { Chip, StatusChip } from "./Chip";
import ui from "./ui.module.css";

const isMock = (v: string) => v.startsWith("mock") || v === "fake";

/** Live backend status from /api/capabilities as glowing chips. Mock providers are dashed and say "mock". */
export function BackendStatus({ align = "center" }: { align?: "center" | "start" }) {
  const { cap, error, waking } = useCapabilities();
  if (error)
    return (
      <div className={ui.statusRow} data-align={align}>
        <Chip tone="bad" dot title={`${API_BASE}: ${error}`}>
          Backend unreachable
        </Chip>
        <button type="button" className={ui.chipBtn} onClick={retryWake}>
          Retry
        </button>
      </div>
    );
  if (!cap)
    return (
      <div className={ui.statusRow} data-align={align} aria-busy="true">
        <Chip tone={waking ? "warn" : "plain"} dot live title={waking ? "The demo backend sleeps when idle and is starting up." : undefined}>
          {waking ? "Waking backend… please wait" : "Checking backend…"}
        </Chip>
      </div>
    );
  const items: { label: string; value: string; tone: "ok" | "mock" | "plain"; title?: string }[] = [
    { label: "Backend", value: `v${cap.version}`, tone: "ok" },
    ...Object.entries(cap.providers)
      .filter(([k]) => k !== "telephony")
      .map(([k, v]) => ({ label: k.toUpperCase(), value: isMock(v) ? "mock" : v.split(":")[0], tone: isMock(v) ? ("mock" as const) : ("ok" as const), title: v })),
    { label: "PSTN", value: cap.telephony.active ? "active" : "off", tone: cap.telephony.active ? "ok" : "plain" },
    { label: "Mic", value: cap.browser_voice_available ? "available" : "typed fallback", tone: cap.browser_voice_available ? "ok" : "plain" },
  ];
  return (
    <div className={ui.statusRow} data-align={align} aria-label="Live backend status">
      {items.map((it, i) => (
        <m.span key={it.label} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.15 + i * 0.05 }}>
          <StatusChip label={it.label} value={it.value} tone={it.tone} live={it.tone === "ok"} title={it.title} />
        </m.span>
      ))}
    </div>
  );
}
