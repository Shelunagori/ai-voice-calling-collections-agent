// @vitest-environment jsdom
/**
 * Telephony page Start Call flow: no token or password field, duplicate-click guard,
 * success shows session/call ids and links to the shared audit page, structured policy
 * blocks render rule + reason, errors are inline.
 */
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PHONE_ID, phoneDetail } from "./fixtures";

vi.mock("next/link", () => ({ default: ({ href, children, className }: { href: string; children: React.ReactNode; className?: string }) => <a href={href} className={className}>{children}</a> }));

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const CAP = { telephony: { active: true, operator_endpoints: true, allowed_numbers_configured: 1, transfer_number_configured: false, enabled_flag: true, configured: true } };
const SCENARIOS = [{ key: "A", title: "Cooperative debtor" }, { key: "B", title: "Needs more time" }];

let accounts: unknown[] = [];
let contactPoints: unknown[] = [];

function install(call: () => Response | Promise<Response>, detail: () => Response = () => json(404, { ok: false, code: "not_found" })) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      if (url.endsWith("/api/capabilities")) return json(200, CAP);
      if (url.endsWith("/api/scenarios")) return json(200, SCENARIOS);
      if (url.endsWith("/api/accounts")) return json(200, accounts);
      if (url.endsWith("/api/contact-points")) return json(200, contactPoints);
      if (url === "/api/operator/calls") return call();
      if (url.startsWith("/api/operator/sessions/")) return detail();
      throw new Error(`unexpected ${url}`);
    }),
  );
  return calls;
}

async function renderPage() {
  const { default: Page } = await import("../app/telephony/page");
  await act(async () => {
    render(<Page />);
  });
  await waitFor(() => expect(screen.getByRole("option", { name: "B — Needs more time" })).toBeTruthy());
}

function fill(to = "+81 90-1234-5678") {
  fireEvent.change(screen.getByPlaceholderText("+819012345678"), { target: { value: to } });
}

beforeEach(() => {
  vi.resetModules();
  accounts = [];
  contactPoints = [];
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Start Call", () => {
  it("has no token/password input and loads scenarios from the API", async () => {
    install(() => json(200, {}));
    await renderPage();
    expect(document.querySelector("input[type=password]")).toBeNull();
    expect(screen.getByText(/Only allowlisted demo numbers can be dialed/)).toBeTruthy();
  });

  it("validates E.164 on the client", async () => {
    const calls = install(() => json(200, {}));
    await renderPage();
    fill("090-1234-5678");
    expect((screen.getByRole("button", { name: "Start Call" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/E.164 format/)).toBeTruthy();
    expect(calls.some((c) => c.url === "/api/operator/calls")).toBe(false);
  });

  it("prevents duplicate submission and renders session/call ids with Open Session", async () => {
    let release!: (r: Response) => void;
    const calls = install(
      () => new Promise<Response>((res) => (release = res)),
      () => json(200, { ok: true, operator: true, detail: { ...phoneDetail(), session: { ...phoneDetail().session, call_status: "IN_PROGRESS", ended_at: null, ended_reason: null } } }),
    );
    await renderPage();
    fill();
    const form = screen.getByRole("form", { name: "Start call" });
    await act(async () => {
      fireEvent.submit(form);
      fireEvent.submit(form);
      fireEvent.submit(form);
    });
    expect(screen.getByRole("button", { name: "Starting call…" })).toBeTruthy();
    expect(calls.filter((c) => c.url === "/api/operator/calls")).toHaveLength(1);
    const sent = calls.find((c) => c.url === "/api/operator/calls")!;
    expect(JSON.parse(String(sent.init?.body))).toEqual({ to: "+819012345678", scenario: "A", language: "en" });
    expect(JSON.stringify(sent.init?.headers)).not.toMatch(/authorization/i);
    await act(async () => {
      release(json(200, { ok: true, outcome: "dialing", session_id: PHONE_ID, call_id: "CA00000000000000000000000000000000", decisions: [{ rule: "MAX_CONTACT_ATTEMPTS", decision: "ALLOW", reason: "0 prior attempts" }] }));
    });
    await waitFor(() => expect(screen.getByTestId("session-id").textContent).toBe(PHONE_ID));
    expect(screen.getByTestId("call-id").textContent).toBe("CA00000000000000000000000000000000");
    expect(screen.getByRole("link", { name: "Open Session" }).getAttribute("href")).toBe(`/sessions?id=${PHONE_ID}`);
    expect(screen.getByText("Max contact attempts")).toBeTruthy();
    await waitFor(() => expect(screen.getByTestId("live-status").textContent).toContain("IN_PROGRESS"));
  });

  it("renders a structured policy block with rule and reason", async () => {
    install(() =>
      json(200, {
        ok: true,
        outcome: "blocked_by_policy",
        decisions: [
          { rule: "DEMO_CALLING_HOURS_WINDOW", decision: "BLOCK", reason: "outside the simulated demo window 08:00-21:00 (POLICY_COUNTRY=JP)" },
          { rule: "MAX_CONTACT_ATTEMPTS", decision: "ALLOW", reason: "0 prior attempts; limit 3" },
          { rule: "STOP_CONTACT_BLOCKS_CONTACT", decision: "ALLOW", reason: "no stop-contact flag" },
        ],
      }),
    );
    await renderPage();
    fill();
    await act(async () => {
      fireEvent.submit(screen.getByRole("form", { name: "Start call" }));
    });
    await waitFor(() => expect(screen.getByTestId("call-blocked")).toBeTruthy());
    expect(screen.getByText(/Call blocked by policy/)).toBeTruthy();
    expect(screen.getByText("Calling hours")).toBeTruthy();
    expect(screen.getByText(/outside the simulated demo window/)).toBeTruthy();
    expect(screen.queryByTestId("call-error")).toBeNull();
  });

  it.each([
    [() => json(403, { ok: false, code: "not_allowlisted" }), "not_allowlisted", /not on the demo allowlist/],
    [() => json(400, { ok: false, code: "invalid_request" }), "invalid_request", /Check the number/],
    [() => json(502, { ok: false, code: "provider_error", detail: "telephony provider error: auth" }), "provider_error", /provider failed.*auth/],
    [() => json(502, { ok: false, code: "backend_unreachable" }), "backend_unreachable", /backend is unavailable/],
    [() => json(504, { ok: false, code: "timeout" }), "timeout", /timed out/],
    [() => new Response("{nope", { status: 200 }), "malformed_response", /malformed/],
    [
      () => {
        throw new TypeError("Failed to fetch");
      },
      "network_error",
      /Network error/,
    ],
  ])("error %# renders inline", async (resp, code, text) => {
    install(resp);
    await renderPage();
    fill();
    await act(async () => {
      fireEvent.submit(screen.getByRole("form", { name: "Start call" }));
    });
    await waitFor(() => expect(screen.getByTestId("call-error").dataset.code).toBe(code));
    expect(screen.getByTestId("call-error").textContent).toMatch(text);
  });
});

describe("Contact eligibility", () => {
  it("shows every account of a stopped debtor and the stopped number as not eligible", async () => {
    accounts = [
      { scenario_key: "A", debtor_name: "Haruto Sato", contact_attempts: 1, stop_contact: false, stop_contact_at: null, debtor_stop_contact: false, eligible: true },
      { scenario_key: "E", debtor_name: "Daiki Ito", contact_attempts: 1, stop_contact: true, stop_contact_at: "2026-09-26T16:40:00", debtor_stop_contact: true, debtor_stop_contact_at: "2026-09-26T16:40:00", eligible: false },
    ];
    contactPoints = [{ label: "+91•••••••008", stop_contact: true, stop_contact_at: "2026-09-26T16:40:00" }];
    install(() => json(200, {}));
    await renderPage();
    await waitFor(() => expect(screen.getByTestId("account-E").textContent).toContain("not eligible · debtor stop-contact"));
    expect(screen.getByTestId("account-A").textContent).toContain("eligible");
    expect(screen.getByTestId("contact-points").textContent).toContain("+91•••••••008");
    expect(screen.getByTestId("contact-points").textContent).toContain("blocked for every scenario");
  });
});
