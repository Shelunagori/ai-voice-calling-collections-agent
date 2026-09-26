/**
 * Passwordless operator architecture: the Next.js server holds OPERATOR_TOKEN and adds it
 * to exactly two backend routes (session detail, start call). The browser never sends,
 * receives or stores it; there is no console password or login cookie.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { CallGuard, proxySessionDetail, proxyStartCall, sameOriginJson, type ServerConfig } from "../lib/server/operator";
import { phoneDetail, PHONE_ID } from "./fixtures";

const TOKEN = "test-operator-token-4f1c9a7e3b";
const CFG: ServerConfig = { token: TOKEN, backend: "https://backend.test" };
const CALL = { to: "+819012345678", scenario: "A", language: "en" };

type BackendFetch = (url: string, init: RequestInit) => Promise<Response>;
function backend(status: number, body: unknown, raw = false) {
  const payload = raw ? String(body) : JSON.stringify(body);
  return vi.fn<BackendFetch>(async () => new Response(payload, { status, headers: { "content-type": "application/json" } }));
}
const headersOf = (f: ReturnType<typeof backend>) => f.mock.calls[0][1].headers as Record<string, string>;
const fresh = () => new CallGuard();
const dialing = { status: "dialing", session_id: PHONE_ID, call_id: "CA00000000000000000000000000000000", decisions: [{ rule: "MAX_CONTACT_ATTEMPTS", decision: "ALLOW", reason: "0 prior attempts; limit 3", session_id: "x", timestamp: "t", decision_id: "d" }] };

describe("session detail proxy (no browser login)", () => {
  it("phone detail opens: the server attaches the token itself", async () => {
    const f = backend(200, phoneDetail());
    const r = await proxySessionDetail(PHONE_ID, { config: CFG, fetchImpl: f });
    expect(r.status).toBe(200);
    expect(r.body).toMatchObject({ ok: true, operator: true });
    expect(f.mock.calls[0][0]).toBe(`https://backend.test/api/sessions/${PHONE_ID}`);
    expect(headersOf(f).Authorization).toBe(`Bearer ${TOKEN}`);
    expect(JSON.stringify(r.body)).not.toContain(TOKEN);
  });
  it("missing server token -> not configured (never a bearer)", async () => {
    const f = backend(401, { detail: "invalid operator token" });
    const r = await proxySessionDetail(PHONE_ID, { config: { ...CFG, token: "" }, fetchImpl: f });
    expect(headersOf(f).Authorization).toBeUndefined();
    expect(r).toMatchObject({ status: 503, body: { code: "operator_not_configured" } });
  });
  it("backend rejects the token", async () => {
    const r = await proxySessionDetail(PHONE_ID, { config: CFG, fetchImpl: backend(401, {}) });
    expect(r).toMatchObject({ status: 502, body: { code: "operator_token_rejected" } });
  });
  it.each([
    [404, { detail: "session not found" }, false, 404, "not_found"],
    [500, { detail: "Traceback… secret" }, false, 502, "backend_error"],
    [200, "{not json", true, 502, "bad_backend_response"],
    [200, { unexpected: true }, false, 502, "bad_backend_response"],
  ])("backend %i -> %s", async (st, body, raw, want, code) => {
    const r = await proxySessionDetail(PHONE_ID, { config: CFG, fetchImpl: backend(st, body, raw) });
    expect(r).toMatchObject({ status: want, body: { ok: false, code } });
    expect(JSON.stringify(r.body)).not.toContain("secret");
  });
  it("only UUID ids reach the backend (no arbitrary proxying)", async () => {
    const f = backend(200, phoneDetail());
    for (const id of ["../../api/operator/reset-demo", "x", `${PHONE_ID}/../../calls`]) {
      expect((await proxySessionDetail(id, { config: CFG, fetchImpl: f })).status).toBe(400);
    }
    expect(f).not.toHaveBeenCalled();
  });
});

describe("start call proxy", () => {
  it("forwards only to/scenario/language with the server token; sanitises the response", async () => {
    const f = backend(200, dialing);
    const r = await proxyStartCall({ ...CALL, to: "+81 90-1234-5678", extra: "x", Authorization: "Bearer evil" }, { config: CFG, fetchImpl: f, guard: fresh() });
    expect(r.status).toBe(200);
    expect(r.body).toEqual({
      ok: true,
      outcome: "dialing",
      session_id: PHONE_ID,
      call_id: "CA00000000000000000000000000000000",
      decisions: [{ rule: "MAX_CONTACT_ATTEMPTS", decision: "ALLOW", reason: "0 prior attempts; limit 3" }],
    });
    const [url, init] = f.mock.calls[0];
    expect(url).toBe("https://backend.test/api/operator/calls");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual(CALL);
    expect((init.headers as Record<string, string>).Authorization).toBe(`Bearer ${TOKEN}`);
    expect(JSON.stringify(r.body)).not.toContain(TOKEN);
  });

  it.each([
    [{ ...CALL, to: "09012345678" }],
    [{ ...CALL, to: "+81 abc" }],
    [{ ...CALL, scenario: "AB" }],
    [{ ...CALL, language: "fr" }],
    ["not an object"],
  ])("invalid input never reaches the backend: %j", async (bad) => {
    const f = backend(200, dialing);
    const r = await proxyStartCall(bad, { config: CFG, fetchImpl: f, guard: fresh() });
    expect(r).toMatchObject({ status: 400, body: { code: "invalid_request" } });
    expect(f).not.toHaveBeenCalled();
  });

  it("structured policy block is passed through as an outcome, not an error", async () => {
    const block = { status: "blocked_by_policy", decisions: [{ rule: "DEMO_CALLING_HOURS_WINDOW", decision: "BLOCK", reason: "outside 08:00-21:00" }, { rule: "STOP_CONTACT_BLOCKS_CONTACT", decision: "ALLOW", reason: "no stop-contact flag" }] };
    const r = await proxyStartCall(CALL, { config: CFG, fetchImpl: backend(200, block), guard: fresh() });
    expect(r).toMatchObject({ status: 200, body: { ok: true, outcome: "blocked_by_policy" } });
    expect((r.body.decisions as unknown[]).length).toBe(2);
  });

  it.each([
    [403, { detail: "destination is not on DEMO_CALL_ALLOWED_NUMBERS" }, 403, "not_allowlisted"],
    [403, { detail: "operator endpoints disabled: OPERATOR_TOKEN is not configured" }, 503, "operator_not_configured"],
    [401, { detail: "invalid operator token" }, 502, "operator_token_rejected"],
    [409, { detail: "telephony disabled" }, 409, "telephony_disabled"],
    [429, { detail: "call rate limit reached" }, 429, "rate_limited"],
    [404, { detail: "unknown scenario" }, 400, "unknown_scenario"],
    [422, { detail: [] }, 400, "invalid_request"],
    [502, { detail: "telephony provider error: auth" }, 502, "provider_error"],
    [500, { detail: "boom secret" }, 502, "backend_error"],
  ])("backend %i %j -> %s", async (st, body, want, code) => {
    const r = await proxyStartCall(CALL, { config: CFG, fetchImpl: backend(st, body), guard: fresh() });
    expect(r).toMatchObject({ status: want, body: { ok: false, code } });
    expect(JSON.stringify(r.body)).not.toContain("secret");
    expect(JSON.stringify(r.body)).not.toContain(TOKEN);
  });

  it("provider error keeps only the error kind", async () => {
    const r = await proxyStartCall(CALL, { config: CFG, fetchImpl: backend(502, { detail: "telephony provider error: auth" }), guard: fresh() });
    expect(r.body.detail).toBe("telephony provider error: auth");
  });

  it("Railway network error and timeout are distinct", async () => {
    const down = vi.fn<BackendFetch>(async () => {
      throw new TypeError("fetch failed");
    });
    expect(await proxyStartCall(CALL, { config: CFG, fetchImpl: down, guard: fresh() })).toMatchObject({ status: 502, body: { code: "backend_unreachable" } });
    const slow = vi.fn<BackendFetch>(
      (_u, init) => new Promise((_, rej) => init.signal?.addEventListener("abort", () => rej(Object.assign(new Error("t"), { name: "TimeoutError" })))),
    );
    expect(await proxyStartCall(CALL, { config: CFG, fetchImpl: slow, guard: fresh(), timeoutMs: 20 })).toMatchObject({ status: 504, body: { code: "timeout" } });
  });

  it("malformed backend JSON and missing server token", async () => {
    expect(await proxyStartCall(CALL, { config: CFG, fetchImpl: backend(200, "{x", true), guard: fresh() })).toMatchObject({ status: 502, body: { code: "bad_backend_response" } });
    const f = backend(200, dialing);
    expect(await proxyStartCall(CALL, { config: { ...CFG, token: "" }, fetchImpl: f, guard: fresh() })).toMatchObject({ status: 503, body: { code: "operator_not_configured" } });
    expect(f).not.toHaveBeenCalled();
  });

  it("rejects rapid duplicates and rate-limits per client", async () => {
    const g = new CallGuard(3, 600_000, 30_000);
    const f = backend(200, dialing);
    const t0 = 1_000_000;
    expect((await proxyStartCall(CALL, { config: CFG, fetchImpl: f, guard: g, client: "ip1", nowMs: t0 })).status).toBe(200);
    expect(await proxyStartCall(CALL, { config: CFG, fetchImpl: f, guard: g, client: "ip2", nowMs: t0 + 5_000 })).toMatchObject({ status: 409, body: { code: "duplicate_request" } });
    expect((await proxyStartCall(CALL, { config: CFG, fetchImpl: f, guard: g, client: "ip1", nowMs: t0 + 31_000 })).status).toBe(200);
    expect((await proxyStartCall(CALL, { config: CFG, fetchImpl: f, guard: g, client: "ip1", nowMs: t0 + 62_000 })).status).toBe(200);
    expect(await proxyStartCall(CALL, { config: CFG, fetchImpl: f, guard: g, client: "ip1", nowMs: t0 + 93_000 })).toMatchObject({ status: 429, body: { code: "rate_limited" } });
    expect(f).toHaveBeenCalledTimes(3);
  });

  it("same-origin JSON check", () => {
    const h = (o: Record<string, string>) => new Headers(o);
    expect(sameOriginJson(h({ origin: "https://c.test", "content-type": "application/json" }), "https://c.test")).toBe(true);
    expect(sameOriginJson(h({ origin: "https://evil.test", "content-type": "application/json" }), "https://c.test")).toBe(false);
    expect(sameOriginJson(h({ "content-type": "application/json" }), "https://c.test")).toBe(false);
    expect(sameOriginJson(h({ origin: "https://c.test", "content-type": "text/plain" }), "https://c.test")).toBe(false);
  });
});

describe("route handlers", () => {
  it("session detail: no Authorization header or token in the response", async () => {
    vi.stubEnv("OPERATOR_TOKEN", TOKEN);
    vi.stubEnv("BACKEND_API_BASE_URL", "https://backend.test");
    vi.stubGlobal("fetch", backend(200, phoneDetail()));
    const { GET } = await import("../app/api/operator/sessions/[id]/route");
    const res = await GET(new NextRequest(`https://console.test/api/operator/sessions/${PHONE_ID}`), { params: Promise.resolve({ id: PHONE_ID }) });
    expect(res.status).toBe(200);
    expect(res.headers.get("authorization")).toBeNull();
    expect(res.headers.get("set-cookie")).toBeNull();
    expect(res.headers.get("cache-control")).toBe("no-store");
    expect(await res.text()).not.toContain(TOKEN);
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("start call: cross-site and non-JSON requests are refused before any backend call", async () => {
    vi.stubEnv("OPERATOR_TOKEN", TOKEN);
    vi.stubEnv("BACKEND_API_BASE_URL", "https://backend.test");
    const f = backend(200, dialing);
    vi.stubGlobal("fetch", f);
    const { POST } = await import("../app/api/operator/calls/route");
    const mk = (headers: Record<string, string>, body = JSON.stringify(CALL)) =>
      new NextRequest("https://console.test/api/operator/calls", { method: "POST", body, headers });
    expect((await POST(mk({ origin: "https://evil.test", "content-type": "application/json" }))).status).toBe(403);
    expect((await POST(mk({ "content-type": "application/json" }))).status).toBe(403); // no Origin (curl, forms)
    expect((await POST(mk({ origin: "https://console.test", "content-type": "text/plain" }))).status).toBe(403);
    expect(f).not.toHaveBeenCalled();
    const ok = await POST(mk({ origin: "https://console.test", "content-type": "application/json", "x-real-ip": "203.0.113.9", authorization: "Bearer from-browser" }));
    expect(ok.status).toBe(200);
    expect((f.mock.calls[0][1].headers as Record<string, string>).Authorization).toBe(`Bearer ${TOKEN}`); // browser header ignored
    const text = await ok.text();
    expect(text).not.toContain(TOKEN);
    expect(ok.headers.get("authorization")).toBeNull();
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });
});

// ------------------------------------------------------------------ no secret in client code
const ROOT = path.resolve(__dirname, "..");
const REPO = path.resolve(ROOT, "..");
function files(dir: string, re = /\.(tsx?|jsx?|mjs)$/, out: string[] = []): string[] {
  for (const n of readdirSync(dir)) {
    if (["node_modules", ".next", ".git", ".venv", "__pycache__"].includes(n)) continue;
    const p = path.join(dir, n);
    if (statSync(p).isDirectory()) files(p, re, out);
    else if (re.test(n)) out.push(p);
  }
  return out;
}

describe("client code never touches server secrets", () => {
  it("only lib/server and app/api read OPERATOR_* env or import lib/server", () => {
    const client = [...files(path.join(ROOT, "app")), ...files(path.join(ROOT, "components")), ...files(path.join(ROOT, "lib"))].filter(
      (p) => !p.includes(`${path.sep}lib${path.sep}server${path.sep}`) && !p.includes(`${path.sep}app${path.sep}api${path.sep}`),
    );
    expect(client.length).toBeGreaterThan(10);
    for (const p of client) {
      const src = readFileSync(p, "utf8");
      expect(src, p).not.toMatch(/process\.env\.(OPERATOR_TOKEN|BACKEND_API_BASE_URL)/);
      expect(src, p).not.toMatch(/lib\/server/);
      expect(src, p).not.toMatch(/NEXT_PUBLIC_OPERATOR/);
      expect(src, p).not.toMatch(/(localStorage|sessionStorage)/);
      expect(src, p).not.toMatch(/Authorization/);
    }
  });

  it("the console-password flow is gone from the whole repository", () => {
    const needle = ["OPERATOR", "CONSOLE", "PASSWORD"].join("_");
    const all = files(REPO, /\.(tsx?|jsx?|mjs|py|md|ya?ml|json|example|toml)$|^\.env\.example$/);
    expect(all.length).toBeGreaterThan(50);
    const hits = all.filter((p) => readFileSync(p, "utf8").includes(needle));
    expect(hits).toEqual([]);
  });

  it("the built browser bundle contains no operator token (runs after `npm run check:bundle`)", () => {
    const staticDir = path.join(ROOT, ".next", "static");
    const canary = process.env.BUNDLE_CANARY_TOKEN;
    let built = false;
    try {
      built = statSync(staticDir).isDirectory();
    } catch {
      built = false;
    }
    if (!canary || !built) return; // covered by `npm run check:bundle` (build with a canary token, then scan)
    for (const p of files(staticDir, /./)) expect(readFileSync(p, "utf8").includes(canary), p).toBe(false);
  });
});
