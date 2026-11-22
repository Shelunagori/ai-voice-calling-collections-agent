// Backend wake-up tracking. The hosted backend runs on a free tier that sleeps when idle; the first request after
// that can take ~30–60 s while it starts. One shared check (GET /api/capabilities, retried with backoff) tells the
// whole UI whether the backend is checking / waking / awake / down, so every page can say so instead of looking
// broken. Client-only, in-memory state (no web storage).
import { API_BASE, type Capabilities } from "./api";

export type WakeStatus = "idle" | "checking" | "waking" | "awake" | "down";
export type WakeSnapshot = {
  status: WakeStatus;
  cap: Capabilities | null;
  error: string | null;
  /** performance.now() when the current check started */
  startedAt: number;
  /** true when the backend only answered after we showed "waking" (lets the UI confirm it is up) */
  wasAsleep: boolean;
};

/** After this long without an answer we tell the user the backend is waking up. */
export const WAKE_NOTICE_MS = 2500;
/** Give up (and offer Retry) after this long. Cold starts are usually well under a minute. */
const DEADLINE_MS = 150_000;
/** Per-attempt timeout: long, because a held request is what wakes the server. */
const ATTEMPT_MS = 30_000;
const BACKOFF_MS = [1500, 2500, 3000];
/** Status codes a waking/restarting host returns before the app is up. */
const TRANSIENT = new Set([502, 503, 504]);

const SERVER_SNAPSHOT: WakeSnapshot = { status: "idle", cap: null, error: null, startedAt: 0, wasAsleep: false };
let snap: WakeSnapshot = SERVER_SNAPSHOT;
let run = 0;
const listeners = new Set<() => void>();
let settledWaiters: (() => void)[] = [];

function set(patch: Partial<WakeSnapshot>) {
  snap = { ...snap, ...patch };
  listeners.forEach((l) => l());
  if (snap.status === "awake" || snap.status === "down") {
    const w = settledWaiters;
    settledWaiters = [];
    w.forEach((f) => f());
  }
}

export function subscribeWake(cb: () => void) {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}
export const getWakeSnapshot = () => snap;
export const getWakeServerSnapshot = () => SERVER_SNAPSHOT;

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function attempt(): Promise<{ ok: true; cap: Capabilities } | { ok: false; transient: boolean; error: string }> {
  try {
    const signal = typeof AbortSignal !== "undefined" && "timeout" in AbortSignal ? AbortSignal.timeout(ATTEMPT_MS) : undefined;
    const r = await fetch(`${API_BASE}/api/capabilities`, { cache: "no-store", signal });
    if (r.ok) return { ok: true, cap: (await r.json()) as Capabilities };
    return { ok: false, transient: TRANSIENT.has(r.status), error: `${r.status}: ${r.statusText}` };
  } catch (e) {
    // network error, CORS failure on the host's interstitial, or our own timeout: all look like "still starting"
    return { ok: false, transient: true, error: (e as Error)?.message || "network error" };
  }
}

async function check() {
  const id = ++run;
  const startedAt = performance.now();
  set({ status: "checking", error: null, startedAt, wasAsleep: false });
  const notice = setTimeout(() => {
    if (id === run && snap.status === "checking") set({ status: "waking", wasAsleep: true });
  }, WAKE_NOTICE_MS);
  try {
    for (let i = 0; ; i++) {
      const res = await attempt();
      if (id !== run) return;
      if (res.ok) {
        set({ status: "awake", cap: res.cap, error: null });
        return;
      }
      if (!res.transient || performance.now() - startedAt > DEADLINE_MS) {
        set({ status: "down", error: res.error });
        return;
      }
      // a fast failure means the host answered but the app is not up yet: that is the waking phase too
      if (snap.status === "checking") set({ status: "waking", wasAsleep: true });
      await sleep(BACKOFF_MS[Math.min(i, BACKOFF_MS.length - 1)]);
      if (id !== run) return;
    }
  } finally {
    clearTimeout(notice);
  }
}

/** Start the shared check once (no-op if it is running or already done). */
export function ensureAwake() {
  if (typeof window === "undefined") return;
  if (snap.status === "idle") void check();
}

/** Check again from scratch (the Retry button). */
export function retryWake() {
  if (typeof window === "undefined") return;
  if (snap.status === "checking" || snap.status === "waking") return;
  void check();
}

/** True while the shared check is still waiting for the backend. */
export const isWaking = () => snap.status === "checking" || snap.status === "waking";

/** Resolves once the check settles; true if the backend is awake. */
export function whenSettled(): Promise<boolean> {
  if (!isWaking()) return Promise.resolve(snap.status === "awake");
  return new Promise((r) => settledWaiters.push(() => r(snap.status === "awake")));
}

/** HTTP statuses a request may see while the backend host is still starting. */
export const isTransientStatus = (status: number) => TRANSIENT.has(status);
