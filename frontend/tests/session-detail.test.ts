import { describe, expect, it, vi } from "vitest";
import { loadSessionDetail } from "../lib/session-detail";
import { phoneDetail, PHONE_ID } from "./fixtures";

const res = (status: number, body: string) => vi.fn(async () => new Response(body, { status }));

describe("loadSessionDetail", () => {
  it("calls the same-origin proxy only, with no credentials header", async () => {
    const f = res(200, JSON.stringify({ ok: true, operator: true, detail: phoneDetail() }));
    const r = await loadSessionDetail(PHONE_ID, f);
    expect(r.kind).toBe("ok");
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`/api/operator/sessions/${PHONE_ID}`);
    expect(init.headers).toBeUndefined();
    expect(init.credentials).toBe("same-origin");
  });
  it.each([
    [401, '{"ok":false,"code":"operator_sign_in_required"}', "operator_sign_in_required"],
    [404, '{"ok":false,"code":"not_found"}', "not_found"],
    [500, "Internal Server Error", "backend_error"],
    [200, "{bad", "malformed_response"],
    [200, '{"ok":true,"detail":{"session":{}}}', "malformed_response"],
    [418, '{"ok":false,"code":"made_up"}', "backend_error"],
  ])("HTTP %i %s -> %s", async (status, body, code) => {
    const r = await loadSessionDetail(PHONE_ID, res(status, body));
    expect(r).toMatchObject({ kind: "error", code });
  });
  it("network failure", async () => {
    const r = await loadSessionDetail(PHONE_ID, vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))));
    expect(r).toMatchObject({ kind: "error", code: "network_error", status: 0 });
  });
});
