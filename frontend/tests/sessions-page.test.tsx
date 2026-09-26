// @vitest-environment jsdom
/**
 * Sessions page: both browser and phone audit details open directly through the
 * same-origin proxy (no password prompt); every failure is an inline message, never a crash.
 */
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { browserDetail, BROWSER_ID, phoneDetail, PHONE_ID } from "./fixtures";

let currentId: string | null = null;
vi.mock("next/navigation", () => ({ useSearchParams: () => ({ get: (k: string) => (k === "id" ? currentId : null) }) }));
vi.mock("next/link", () => ({ default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a> }));

type Handler = (url: string, init?: RequestInit) => Response | Promise<Response>;
const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

function installFetch(detail: Handler) {
  const calls: { url: string; init?: RequestInit }[] = [];
  const f = vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    if (url.startsWith("/api/operator/sessions/")) return detail(url, init);
    throw new Error(`unexpected ${url}`);
  });
  vi.stubGlobal("fetch", f);
  return calls;
}

async function renderPage(id: string) {
  currentId = id;
  const { default: Page } = await import("../app/sessions/page");
  await act(async () => {
    render(<Page />);
  });
}

beforeEach(() => vi.resetModules());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("session detail page", () => {
  it("opens a browser session without sign-in", async () => {
    installFetch(() => json(200, { ok: true, operator: false, detail: browserDetail() }));
    await renderPage(BROWSER_ID);
    await waitFor(() => expect(screen.getByTestId("session-detail")).toBeTruthy());
    expect(screen.getByTestId("channel-pill").textContent).toBe("browser");
    expect(screen.getAllByText("completed_with_promise", { selector: "dd" }).length).toBeGreaterThan(0);
  });

  it("phone session opens directly with the full audit layout (no password prompt)", async () => {
    const calls = installFetch(() => json(200, { ok: true, operator: true, detail: phoneDetail() }));
    await renderPage(PHONE_ID);
    await waitFor(() => expect(screen.getByTestId("session-detail")).toBeTruthy());
    expect(screen.queryByPlaceholderText(/password/i)).toBeNull();
    expect(document.querySelector("input[type=password]")).toBeNull();
    expect(screen.getByTestId("channel-pill").textContent).toBe("phone (PSTN)");
    for (const section of ["Session", "Identity", "Voice runtime", "Latency", "Collection state", "End state"]) {
      expect(screen.getByRole("region", { name: section })).toBeTruthy();
    }
    expect(screen.getByText("CA00000000000000000000000000000000")).toBeTruthy();
    expect(screen.getByText("DOB partial")).toBeTruthy();
    expect(screen.getByText("PARTIAL_DOB (year, month)")).toBeTruthy();
    expect(document.body.textContent).not.toContain("1988-04-12");
    expect(calls).toHaveLength(1);
    for (const c of calls) expect(JSON.stringify(c.init?.headers ?? {})).not.toMatch(/authorization/i);
  });

  it.each([
    [json(404, { ok: false, code: "not_found" }), "not_found"],
    [json(502, { ok: false, code: "backend_error" }), "backend_error"],
    [json(503, { ok: false, code: "operator_not_configured" }), "operator_not_configured"],
    [new Response("<html>gateway</html>", { status: 502 }), "backend_error"],
    [new Response("{oops", { status: 200 }), "malformed_response"],
  ])("renders an inline error for %#", async (resp, code) => {
    installFetch(() => resp);
    await renderPage(PHONE_ID);
    await waitFor(() => expect(screen.getByTestId("detail-error").dataset.code).toBe(code));
  });

  it("network failure is an inline error with retry", async () => {
    installFetch(() => {
      throw new TypeError("Failed to fetch");
    });
    await renderPage(PHONE_ID);
    await waitFor(() => expect(screen.getByTestId("detail-error").dataset.code).toBe("network_error"));
    expect(screen.getByText("Retry")).toBeTruthy();
  });
});
