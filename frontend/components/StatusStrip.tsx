"use client";

// Live backend capabilities (GET /api/capabilities), shared by every page that shows backend status. Backed by the
// shared wake-up check in lib/backend-wake: the hosted backend sleeps when idle, so the first check can take a while
// and is retried instead of failing on the first attempt.

import { useEffect, useSyncExternalStore } from "react";
import { ensureAwake, getWakeServerSnapshot, getWakeSnapshot, subscribeWake } from "@/lib/backend-wake";

export function useBackendWake() {
  const snap = useSyncExternalStore(subscribeWake, getWakeSnapshot, getWakeServerSnapshot);
  useEffect(() => {
    ensureAwake();
  }, []);
  return snap;
}

export function useCapabilities() {
  const snap = useBackendWake();
  return { cap: snap.cap, error: snap.status === "down" ? snap.error : null, waking: snap.status === "waking" };
}
