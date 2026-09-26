// Backend location. NEXT_PUBLIC_API_BASE_URL is inlined at build time and is not a
// secret: all provider credentials stay on the server.
export const API_BASE = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");

/** WebSocket URL on the *backend* host (never the frontend origin): http->ws, https->wss. */
export function wsUrl(path: string, base: string = API_BASE): string {
  const u = new URL(base);
  if (u.protocol !== "https:" && u.protocol !== "http:") throw new Error(`unsupported API base ${base}`);
  const proto = u.protocol === "https:" ? "wss:" : "ws:";
  const prefix = u.pathname.replace(/\/+$/, "");
  const rel = path.startsWith("/") ? path : `/${path}`;
  return `${proto}//${u.host}${prefix}${rel}`;
}

export async function getJSON<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(API_BASE + path, { cache: "no-store", ...init });
  if (!r.ok) {
    let detail = r.statusText;
    try {
      detail = (await r.json()).detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new Error(`${r.status}: ${detail}`);
  }
  return (await r.json()) as T;
}

export type Capabilities = {
  version: string;
  disclaimer: string;
  providers: Record<string, string>;
  mock_mode: Record<string, boolean>;
  browser_voice_available: boolean;
  real_tts: boolean;
  response_mode: string;
  telephony: {
    enabled_flag: boolean;
    configured: boolean;
    active: boolean;
    operator_endpoints: boolean;
    allowed_numbers_configured: number;
    transfer_number_configured: boolean;
  };
  languages: string[];
  latency_budget_ms: number;
  policy: Record<string, string | number>;
  transcript_retention_days: number;
  raw_audio_persisted: boolean;
};

export type Scenario = {
  key: string;
  title: string;
  title_ja: string;
  summary: string;
  debtor_name: string;
  debtor_name_ja: string;
  synthetic_date_of_birth: string;
  outstanding_balance: number;
  currency: string;
  allowed_min_payment: number;
  max_extension_days: number;
  script_en: string[];
  script_ja: string[];
  tags: string[];
};

export type LatencySummary = {
  budget_ms: number;
  note: string;
  groups: Record<string, Record<string, { count: number; p50_ms: number | null; p95_ms: number | null; max_ms: number | null }>>;
};
