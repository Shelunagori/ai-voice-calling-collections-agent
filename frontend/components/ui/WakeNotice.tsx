"use client";

// Site-wide notice for the hosted backend's cold start. Rendered once in the root layout, so every page starts the
// shared wake-up check and shows the same message: nothing while the backend answers quickly; "waking up, please
// wait" with a live seconds counter if it is slow; a short "awake" confirmation; or "unreachable" with Retry.
import { useEffect, useState } from "react";
import { useBackendWake } from "@/components/StatusStrip";
import { retryWake } from "@/lib/backend-wake";
import { cx } from "./cx";
import ui from "./ui.module.css";

const AWAKE_SHOW_MS = 3500;

export function WakeNotice() {
  const snap = useBackendWake();
  const waking = snap.status === "waking";
  const [elapsed, setElapsed] = useState(0);
  // notices the user (or the timer) has closed, keyed by the check that produced them
  const [closed, setClosed] = useState<string | null>(null);
  const key = `${snap.status}@${snap.startedAt}`;

  useEffect(() => {
    if (!waking) return;
    const tick = () => setElapsed(Math.max(0, Math.round((performance.now() - snap.startedAt) / 1000)));
    tick();
    const t = setInterval(tick, 1000);
    return () => clearInterval(t);
  }, [waking, snap.startedAt]);

  const awakeNow = snap.status === "awake" && snap.wasAsleep;
  useEffect(() => {
    if (!awakeNow) return;
    const t = setTimeout(() => setClosed(key), AWAKE_SHOW_MS);
    return () => clearTimeout(t);
  }, [awakeNow, key]);

  const show = (waking || awakeNow || snap.status === "down") && closed !== key;
  if (!show) return null;

  if (waking)
    return (
      <div className={ui.wake} role="status" aria-live="polite" data-testid="backend-waking">
        <span className={ui.wakeSpin} aria-hidden="true" />
        <span className={ui.wakeText}>
          <b>Waking up the backend… please wait a few seconds</b>
          <small>
            The free demo server sleeps when idle; the first load can take up to a minute.{" "}
            <span className={ui.mono}>{elapsed}s</span>
          </small>
        </span>
      </div>
    );

  if (awakeNow)
    return (
      <div className={cx(ui.wake, ui.wakeOk)} role="status" aria-live="polite">
        <span className={ui.wakeDot} aria-hidden="true" />
        <span className={ui.wakeText}>
          <b>Backend is awake</b>
          <small>Everything is ready.</small>
        </span>
      </div>
    );

  return (
    <div className={cx(ui.wake, ui.wakeBad)} role="alert">
      <span className={ui.wakeDot} aria-hidden="true" />
      <span className={ui.wakeText}>
        <b>Backend unreachable</b>
        <small>It did not respond. It may still be starting; try again in a moment.</small>
      </span>
      <button type="button" className={ui.chipBtn} onClick={retryWake}>
        Retry
      </button>
      <button type="button" className={ui.wakeClose} onClick={() => setClosed(key)} aria-label="Dismiss">
        ×
      </button>
    </div>
  );
}
