/**
 * Operator auth architecture: the Next.js server holds OPERATOR_TOKEN and adds it to the
 * backend request only for a signed-in operator (HttpOnly signed cookie). These tests pin:
 * success, missing server token, backend rejecting the token, 404, 5xx, malformed JSON,
 * network failure, and that the token never appears in anything returned to the browser.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import {
  issueSession,
  passwordMatches,
  proxySessionDetail,
  type ServerConfig,
  verifySession,
} from "../lib/server/operator";
import { phoneDetail, PHONE_ID } from "./fixtures";

const TOKEN = "test-operator-token-4f1c9a7e3b";
const CFG: ServerConfig = { token: TOKEN, password: "console-pass", backend: "https://backend.test" };
const cookie = () => issueSession(TOKEN);

type BackendFetch = (url: string, init: RequestInit) => Promise<Response>;
function backend(status: number, body: unknown, raw = false) {
  const payload = raw ? String(body) : JSON.stringify(body);
  return vi.fn<BackendFetch>(async () => new Response(payload, { status, headers: { "content-type": "application/json" } }));
}
const authOf = (f: ReturnType<typeof backend>) => (f.mock.calls[0][1].headers as Record<string, string>).Authorization;

describe("session cookie", () => {
  it("round-trips, expires, and rejects tampering or a rotated token", () => {
    const now = Date.now();
    const c = issueSession(TOKEN, now);
    expect(verifySession(c, TOKEN, now)).toBe(true);
    expect(verifySession(c, TOKEN, now + 9 * 3600_000)).toBe(false);
    expect(verifySession(c.slice(0, -1) + (c.endsWith("A") ? "B" : "A"), TOKEN, now)).toBe(false);
    expect(verifySession(c, "rotated-token", now)).toBe(false);
    expect(verifySession(undefined, TOKEN, now)).toBe(false);
    expect(c).not.toContain(TOKEN);
  });
  it("compares passwords in constant time and rejects non-strings", () => {
    expect(passwordMatches("console-pass", "console-pass")).toBe(true);
    expect(passwordMatches("console-pas", "console-pass")).toBe(false);
    expect(passwordMatches(undefined, "console-pass")).toBe(false);
    expect(passwordMatches("x", "")).toBe(false);
  });
});

describe("proxySessionDetail", () => {
  it("success: signed-in operator gets phone detail; token added server-side only", async () => {
    const f = backend(200, phoneDetail());
    const r = await proxySessionDetail(PHONE_ID, { cookie: cookie(), config: CFG, fetchImpl: f });
    expect(r.status).toBe(200);
    expect(r.body.ok).toBe(true);
    expect(r.body.operator).toBe(true);
    expect(f.mock.calls[0][0]).toBe(`https://backend.test/api/sessions/${PHONE_ID}`);
    expect(authOf(f)).toBe(`Bearer ${TOKEN}`);
    expect(JSON.stringify(r.body)).not.toContain(TOKEN);
  });

  it("not signed in: no Authorization is sent; phone session -> sign-in required", async () => {
    const f = backend(401, { detail: "invalid operator token" });
    const r = await proxySessionDetail(PHONE_ID, { config: CFG, fetchImpl: f });
    expect(authOf(f)).toBeUndefined();
    expect(r).toMatchObject({ status: 401, body: { code: "operator_sign_in_required" } });
  });

  it("missing server-side OPERATOR_TOKEN: never sends a bearer, reports not configured", async () => {
    const f = backend(401, { detail: "invalid operator token" });
    const r = await proxySessionDetail(PHONE_ID, { cookie: cookie(), config: { ...CFG, token: "" }, fetchImpl: f });
    expect(authOf(f)).toBeUndefined();
    expect(r).toMatchObject({ status: 503, body: { code: "operator_not_configured" } });
  });

  it("backend rejects the server's token (Vercel/Railway mismatch)", async () => {
    const f = backend(401, { detail: "invalid operator token" });
    const r = await proxySessionDetail(PHONE_ID, { cookie: cookie(), config: CFG, fetchImpl: f });
    expect(r).toMatchObject({ status: 502, body: { code: "operator_token_rejected" } });
    expect(JSON.stringify(r.body)).not.toContain(TOKEN);
  });

  it.each([
    [404, { detail: "session not found" }, false, 404, "not_found"],
    [500, { detail: "Traceback… secret" }, false, 502, "backend_error"],
    [200, "{not json", true, 502, "bad_backend_response"],
    [200, { unexpected: true }, false, 502, "bad_backend_response"],
  ])("backend %i -> %s", async (st, body, raw, want, code) => {
    const r = await proxySessionDetail(PHONE_ID, { cookie: cookie(), config: CFG, fetchImpl: backend(st, body, raw) });
    expect(r).toMatchObject({ status: want, body: { ok: false, code } });
    expect(JSON.stringify(r.body)).not.toContain("secret");
  });

  it("network failure -> backend_unreachable; invalid id never reaches the backend", async () => {
    const down = vi.fn(async () => {
      throw new TypeError("fetch failed");
    });
    expect(await proxySessionDetail(PHONE_ID, { config: CFG, fetchImpl: down })).toMatchObject({ status: 504, body: { code: "backend_unreachable" } });
    const f = backend(200, phoneDetail());
    expect(await proxySessionDetail("../../api/operator/reset-demo", { config: CFG, fetchImpl: f })).toMatchObject({ status: 400 });
    expect(f).not.toHaveBeenCalled();
  });
});

describe("route handler", () => {
  it("returns no Authorization header, no token, and no-store", async () => {
    vi.stubEnv("OPERATOR_TOKEN", TOKEN);
    vi.stubEnv("OPERATOR_CONSOLE_PASSWORD", "console-pass");
    vi.stubEnv("BACKEND_API_BASE_URL", "https://backend.test");
    const f = backend(200, phoneDetail());
    vi.stubGlobal("fetch", f);
    const { GET } = await import("../app/api/operator/sessions/[id]/route");
    const req = new NextRequest(`https://console.test/api/operator/sessions/${PHONE_ID}`, { headers: { cookie: `vca_operator=${cookie()}` } });
    const res = await GET(req, { params: Promise.resolve({ id: PHONE_ID }) });
    expect(res.status).toBe(200);
    expect(res.headers.get("authorization")).toBeNull();
    expect(res.headers.get("cache-control")).toBe("no-store");
    expect(await res.text()).not.toContain(TOKEN);
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("login sets an HttpOnly SameSite=Strict cookie and rejects a wrong password / foreign origin", async () => {
    vi.stubEnv("OPERATOR_TOKEN", TOKEN);
    vi.stubEnv("OPERATOR_CONSOLE_PASSWORD", "console-pass");
    const { POST } = await import("../app/api/operator/session/route");
    const mk = (password: string, origin = "https://console.test") =>
      new NextRequest("https://console.test/api/operator/session", { method: "POST", body: JSON.stringify({ password }), headers: { origin, "content-type": "application/json" } });
    const ok = await POST(mk("console-pass"));
    const set = ok.headers.get("set-cookie") ?? "";
    expect(ok.status).toBe(200);
    expect(set).toMatch(/vca_operator=v1\./);
    expect(set).toMatch(/HttpOnly/i);
    expect(set).toMatch(/SameSite=strict/i);
    expect(set).not.toContain(TOKEN);
    expect((await POST(mk("nope"))).status).toBe(401);
    expect((await POST(mk("console-pass", "https://evil.test"))).status).toBe(403);
    vi.unstubAllEnvs();
  });
});

// ------------------------------------------------------------------ no secret in client code
const ROOT = path.resolve(__dirname, "..");
function files(dir: string, out: string[] = []): string[] {
  for (const n of readdirSync(dir)) {
    const p = path.join(dir, n);
    if (statSync(p).isDirectory()) files(p, out);
    else if (/\.(tsx?|jsx?|mjs)$/.test(n)) out.push(p);
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
      expect(src, p).not.toMatch(/process\.env\.(OPERATOR_TOKEN|OPERATOR_CONSOLE_PASSWORD|BACKEND_API_BASE_URL)/);
      expect(src, p).not.toMatch(/lib\/server/);
      expect(src, p).not.toMatch(/NEXT_PUBLIC_OPERATOR/);
      expect(src, p).not.toMatch(/(localStorage|sessionStorage)\.setItem\([^)]*(token|password)/i);
    }
  });

  it("the built browser bundle contains no operator token (runs after `npm run build`)", () => {
    const staticDir = path.join(ROOT, ".next", "static");
    const canary = process.env.BUNDLE_CANARY_TOKEN;
    if (!canary || !(() => { try { return statSync(staticDir).isDirectory(); } catch { return false; } })()) {
      return; // covered by `npm run check:bundle` (build with a canary token, then scan)
    }
    for (const p of files(staticDir)) expect(readFileSync(p, "utf8").includes(canary), p).toBe(false);
  });
});
