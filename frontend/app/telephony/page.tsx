"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useCapabilities } from "@/components/StatusStrip";
import { getJSON, Scenario } from "@/lib/api";
import { PolicyDecision, startCall, StartCallResult, statusFromDetail } from "@/lib/operator-calls";
import { normaliseE164 } from "@/lib/phone";
import { loadSessionDetail } from "@/lib/session-detail";

type Account = {
  scenario_key: string;
  debtor_name?: string;
  contact_attempts: number;
  stop_contact: boolean;
  stop_contact_at: string | null;
  debtor_stop_contact?: boolean;
  debtor_stop_contact_at?: string | null;
  eligible?: boolean;
};
type ContactPoint = { label: string; stop_contact: boolean; stop_contact_at: string | null };

const RULE_LABELS: Record<string, string> = {
  DEMO_CALLING_HOURS_WINDOW: "Calling hours",
  MAX_CONTACT_ATTEMPTS: "Max contact attempts",
  STOP_CONTACT_BLOCKS_CONTACT: "Stop contact",
};
const POLL_MS = 3000;
const POLL_MAX_MS = 5 * 60_000;

function Decisions({ items }: { items: PolicyDecision[] }) {
  if (!items.length) return null;
  return (
    <table className="small" data-testid="policy-decisions">
      <tbody>
        {items.map((d) => (
          <tr key={d.rule}>
            <td>{RULE_LABELS[d.rule] ?? d.rule}</td>
            <td>
              <span className={`pill ${d.decision === "BLOCK" ? "bad" : d.decision === "ALLOW" ? "ok" : "muted"}`}>{d.decision}</span>
            </td>
            <td className="muted">{d.reason}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function LiveStatus({ sessionId }: { sessionId: string }) {
  const [status, setStatus] = useState<{ status: string; terminal: boolean; endedReason?: string }>({ status: "waiting_for_answer", terminal: false });
  const [tick, setTick] = useState(0); // "Refresh status" restarts polling
  const [gaveUp, setGaveUp] = useState(false);
  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const started = Date.now();
    const poll = async () => {
      const s = statusFromDetail(await loadSessionDetail(sessionId));
      if (!live) return;
      setStatus(s);
      if (s.terminal) return;
      if (Date.now() - started > POLL_MAX_MS) {
        setGaveUp(true);
        return;
      }
      timer = setTimeout(poll, POLL_MS);
    };
    timer = setTimeout(poll, 0);
    return () => {
      live = false;
      if (timer) clearTimeout(timer);
    };
  }, [sessionId, tick]);
  const label = status.status === "waiting_for_answer" ? "waiting for the call to connect" : status.status;
  return (
    <div className="row small" data-testid="live-status">
      <span>Call status:</span>
      <span className={`pill ${status.terminal ? "muted" : "info"}`}>{label}</span>
      {status.endedReason && <span className="muted mono">{status.endedReason}</span>}
      {(status.terminal || gaveUp) && (
        <button
          className="btn sm"
          onClick={() => {
            setGaveUp(false);
            setTick((n) => n + 1);
          }}
        >
          Refresh status
        </button>
      )}
    </div>
  );
}

export default function TelephonyPage() {
  const { cap } = useCapabilities();
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [contactPoints, setContactPoints] = useState<ContactPoint[]>([]);
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [to, setTo] = useState("");
  const [scenario, setScenario] = useState("A");
  const [language, setLanguage] = useState("en");
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false); // guards double clicks before React re-renders the disabled button
  const [result, setResult] = useState<StartCallResult | null>(null);

  const refreshAccounts = () =>
    Promise.all([
      getJSON<Account[]>("/api/accounts")
        .then((a) => setAccounts(Array.isArray(a) ? a : []))
        .catch(() => undefined),
      getJSON<ContactPoint[]>("/api/contact-points")
        .then((c) => setContactPoints(Array.isArray(c) ? c : []))
        .catch(() => undefined),
    ]);
  useEffect(() => {
    void refreshAccounts();
    getJSON<Scenario[]>("/api/scenarios")
      .then((s) => setScenarios(Array.isArray(s) ? s : []))
      .catch(() => setScenarios([]));
  }, []);

  const normalised = normaliseE164(to);
  const t = cap?.telephony;

  async function onStart(e: React.FormEvent) {
    e.preventDefault();
    if (inFlight.current || !normalised) return;
    inFlight.current = true;
    setBusy(true);
    setResult(null);
    try {
      setResult(await startCall({ to: normalised, scenario, language }));
      void refreshAccounts();
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  return (
    <main className="wrap narrow stack">
      <h1 style={{ fontSize: 22 }}>Telephony</h1>
      <div className="grid cols-2">
        <form className="panel stack" onSubmit={onStart} aria-label="Start call">
          <h3>Start a demo call</h3>
          <label className="small">
            Destination (E.164)
            <input type="tel" placeholder="+819012345678" value={to} onChange={(e) => setTo(e.target.value)} autoComplete="off" />
          </label>
          <p className="small muted" style={{ margin: 0 }}>
            Only allowlisted demo numbers can be dialed{t ? ` (${t.allowed_numbers_configured} configured on the backend)` : ""}.
          </p>
          {to && !normalised && (
            <p className="small" role="alert" style={{ margin: 0 }}>
              Enter the number in E.164 format, e.g. +819012345678.
            </p>
          )}
          <div className="row">
            <label className="small">
              Scenario{" "}
              <select value={scenario} onChange={(e) => setScenario(e.target.value)} style={{ width: "auto" }} aria-label="Scenario">
                {(scenarios.length ? scenarios : [{ key: "A", title: "" } as Scenario]).map((s) => (
                  <option key={s.key} value={s.key}>
                    {s.key}
                    {s.title ? ` — ${s.title}` : ""}
                  </option>
                ))}
              </select>
            </label>
            <label className="small">
              Language{" "}
              <select value={language} onChange={(e) => setLanguage(e.target.value)} style={{ width: "auto" }} aria-label="Language">
                <option value="en">English</option>
                <option value="ja">Japanese</option>
              </select>
            </label>
          </div>
          <button className="btn primary" type="submit" disabled={busy || !normalised}>
            {busy ? "Starting call…" : "Start Call"}
          </button>
          <p className="small muted" style={{ margin: 0 }}>
            The call is placed server-to-server; the backend checks the allowlist and the contact policy (calling window, attempt limit,
            stop-contact) before dialling. Blocked calls are never placed.
          </p>
        </form>

        <div className="panel stack" aria-live="polite">
          <h3>Result</h3>
          {!result && !busy && <p className="muted small">No call started yet.</p>}
          {busy && <p className="small">Starting call…</p>}
          {result?.kind === "dialing" && (
            <div className="stack" data-testid="call-dialing">
              <dl className="kv">
                <dt>Status</dt>
                <dd>Dialing</dd>
                <dt>Session</dt>
                <dd data-testid="session-id">{result.sessionId}</dd>
                <dt>Twilio call</dt>
                <dd data-testid="call-id">{result.callId ?? "—"}</dd>
              </dl>
              <LiveStatus sessionId={result.sessionId} />
              <Decisions items={result.decisions} />
              <Link className="btn" href={`/sessions?id=${result.sessionId}`}>
                Open Session
              </Link>
            </div>
          )}
          {result?.kind === "blocked" && (
            <div className="stack" data-testid="call-blocked">
              <div className="banner" role="alert">
                Call blocked by policy — nothing was dialled.
              </div>
              <Decisions items={result.decisions} />
            </div>
          )}
          {result?.kind === "error" && (
            <div className="banner" role="alert" data-testid="call-error" data-code={result.code}>
              {result.message} {result.status ? <span className="muted mono">(HTTP {result.status})</span> : null}
            </div>
          )}
        </div>
      </div>

      <div className="panel">
        <h3>Backend status</h3>
        <dl className="kv">
          <dt>Telephony</dt>
          <dd>
            <span className={`pill ${t?.active ? "ok" : "muted"}`}>{t?.active ? "active" : "disabled"}</span>
          </dd>
          <dt>Operator endpoints</dt>
          <dd>{t ? (t.operator_endpoints ? "enabled" : "disabled") : "—"}</dd>
          <dt>Allow-listed numbers</dt>
          <dd>{t?.allowed_numbers_configured ?? 0}</dd>
          <dt>Transfer number</dt>
          <dd>{t?.transfer_number_configured ? "configured" : "not configured (transfer is simulated)"}</dd>
        </dl>
        {!t?.active && (
          <p className="small muted" style={{ marginTop: 10 }}>
            Real phone calls need <code>TELEPHONY_ENABLED=true</code>, the <code>TWILIO_*</code> variables, <code>OPERATOR_TOKEN</code> and{" "}
            <code>DEMO_CALL_ALLOWED_NUMBERS</code> on the backend, and the same <code>OPERATOR_TOKEN</code> as a server-only variable on the
            frontend. The browser demo does not depend on any of this.
          </p>
        )}
      </div>

      <div className="panel table-scroll" data-testid="eligibility">
        <h3>Contact eligibility</h3>
        <p className="small muted" style={{ marginTop: 0 }}>
          A stop-contact request applies to the debtor (all of their accounts) and to the phone number where it was heard: any later call to
          that number is blocked for every scenario.
        </p>
        <table>
          <thead>
            <tr>
              <th>Scenario</th>
              <th>Synthetic debtor</th>
              <th>Attempts</th>
              <th>Future contact</th>
            </tr>
          </thead>
          <tbody>
            {accounts.map((a) => {
              const eligible = a.eligible ?? !(a.stop_contact || a.debtor_stop_contact);
              const since = (a.debtor_stop_contact_at || a.stop_contact_at)?.slice(0, 16);
              return (
                <tr key={a.scenario_key} data-testid={`account-${a.scenario_key}`}>
                  <td>{a.scenario_key}</td>
                  <td>{a.debtor_name ?? "—"}</td>
                  <td>{a.contact_attempts}</td>
                  <td>
                    {eligible ? (
                      <span className="pill ok">eligible</span>
                    ) : (
                      <span className="pill bad">
                        not eligible · {a.debtor_stop_contact ? "debtor" : "account"} stop-contact{since ? ` since ${since}` : ""}
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <h4 className="small muted">Phone numbers with a stop-contact request</h4>
        {contactPoints.filter((c) => c.stop_contact).length === 0 ? (
          <p className="small muted" data-testid="contact-points-empty">
            None.
          </p>
        ) : (
          <table className="small" data-testid="contact-points">
            <tbody>
              {contactPoints
                .filter((c) => c.stop_contact)
                .map((c) => (
                  <tr key={c.label}>
                    <td className="mono">{c.label}</td>
                    <td>
                      <span className="pill bad">blocked for every scenario</span>
                    </td>
                    <td className="muted">since {c.stop_contact_at?.slice(0, 16) ?? "—"}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        )}
      </div>
    </main>
  );
}
