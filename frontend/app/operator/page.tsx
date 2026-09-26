"use client";

import { useEffect, useState } from "react";
import { useCapabilities } from "@/components/StatusStrip";
import { getJSON } from "@/lib/api";

type Account = { scenario_key: string; contact_attempts: number; stop_contact: boolean; stop_contact_at: string | null };

export default function OperatorPage() {
  const { cap } = useCapabilities();
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [token, setToken] = useState(""); // kept in memory only, never persisted
  const [to, setTo] = useState("");
  const [scenario, setScenario] = useState("A");
  const [language, setLanguage] = useState("ja");
  const [result, setResult] = useState<string>("");

  const refresh = () => getJSON<Account[]>("/api/accounts").then(setAccounts).catch(() => undefined);
  useEffect(() => {
    void refresh();
  }, []);

  async function call() {
    setResult("");
    try {
      const r = await getJSON<Record<string, unknown>>("/api/operator/calls", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ to, scenario, language }),
      });
      setResult(JSON.stringify(r, null, 2));
      void refresh();
    } catch (e) {
      setResult((e as Error).message);
    }
  }

  async function reset() {
    try {
      await getJSON("/api/operator/reset-demo", { method: "POST", headers: { Authorization: `Bearer ${token}` } });
      setResult("Demo accounts reset.");
      void refresh();
    } catch (e) {
      setResult((e as Error).message);
    }
  }

  const t = cap?.telephony;
  return (
    <main className="wrap narrow stack">
      <h1 style={{ fontSize: 22 }}>Telephony (operator)</h1>
      <div className="grid cols-2">
        <div className="panel">
          <h3>Status</h3>
          <dl className="kv">
            <dt>TELEPHONY_ENABLED</dt>
            <dd>{String(t?.enabled_flag ?? "—")}</dd>
            <dt>Twilio configured</dt>
            <dd>{String(t?.configured ?? "—")}</dd>
            <dt>Active</dt>
            <dd>
              <span className={`pill ${t?.active ? "ok" : "muted"}`}>{t?.active ? "active" : "disabled"}</span>
            </dd>
            <dt>Operator token set</dt>
            <dd>{String(t?.operator_endpoints ?? "—")}</dd>
            <dt>Allow-listed numbers</dt>
            <dd>{t?.allowed_numbers_configured ?? 0}</dd>
            <dt>Transfer number</dt>
            <dd>{t?.transfer_number_configured ? "configured" : "not configured (transfer is simulated)"}</dd>
          </dl>
          {!t?.active && (
            <p className="small muted" style={{ marginTop: 10 }}>
              Real phone calls need <code>TELEPHONY_ENABLED=true</code>, <code>TWILIO_ACCOUNT_SID</code>, <code>TWILIO_AUTH_TOKEN</code>, <code>TWILIO_PHONE_NUMBER</code>,{" "}
              <code>TWILIO_WEBHOOK_BASE_URL</code>, <code>OPERATOR_TOKEN</code> and <code>DEMO_CALL_ALLOWED_NUMBERS</code>. Trial accounts can only call verified
              numbers. The browser demo does not depend on any of this.
            </p>
          )}
        </div>
        <div className="panel stack">
          <h3>Place a demo call</h3>
          <input type="password" placeholder="Operator token" value={token} onChange={(e) => setToken(e.target.value)} autoComplete="off" />
          <input type="text" placeholder="+81… (must be on the allow-list)" value={to} onChange={(e) => setTo(e.target.value)} />
          <div className="row">
            <select value={scenario} onChange={(e) => setScenario(e.target.value)} style={{ width: "auto" }}>
              {"ABCDEFG".split("").map((k) => (
                <option key={k}>{k}</option>
              ))}
            </select>
            <select value={language} onChange={(e) => setLanguage(e.target.value)} style={{ width: "auto" }}>
              <option value="ja">日本語</option>
              <option value="en">English</option>
            </select>
            <button className="btn primary" onClick={call} disabled={!t?.active || !token || !to}>
              Call
            </button>
            <button className="btn" onClick={reset} disabled={!token}>
              Reset demo accounts
            </button>
          </div>
          <p className="small muted">Contact policy (calling window, attempt limit, stop-contact) is evaluated before dialling; blocked calls are never placed.</p>
          {result && <pre className="mono small" style={{ whiteSpace: "pre-wrap", margin: 0 }}>{result}</pre>}
        </div>
      </div>
      <div className="panel table-scroll">
        <h3>Contact eligibility (synthetic accounts)</h3>
        <table>
          <thead>
            <tr>
              <th>Account</th>
              <th>Attempts</th>
              <th>Stop-contact</th>
            </tr>
          </thead>
          <tbody>
            {accounts.map((a) => (
              <tr key={a.scenario_key}>
                <td>{a.scenario_key}</td>
                <td>{a.contact_attempts}</td>
                <td>{a.stop_contact ? <span className="pill bad">active since {a.stop_contact_at?.slice(0, 16)}</span> : <span className="pill ok">no</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </main>
  );
}
