"use client";

// Telephony console. Start Call posts to the same-origin operator proxy; the backend checks the allowlist and the
// contact policy before dialling. Logic and test hooks are unchanged from the previous page; the layout is new.
import { AnimatePresence, m } from "framer-motion";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useCapabilities } from "@/components/StatusStrip";
import t$ from "@/components/telephony/telephony.module.css";
import { Chip, StatusChip } from "@/components/ui/Chip";
import { Cta } from "@/components/ui/Cta";
import { cx } from "@/components/ui/cx";
import { MotionRoot, SPRING } from "@/components/ui/motion";
import ui from "@/components/ui/ui.module.css";
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
const STATUS_LABEL: Record<string, string> = {
  waiting_for_answer: "Waiting for the call to connect",
  IN_PROGRESS: "In progress",
  COMPLETED: "Completed",
  DISCONNECTED: "Disconnected",
  FAILED: "Failed",
};
const POLL_MS = 3000;
const POLL_MAX_MS = 5 * 60_000;

function Decisions({ items }: { items: PolicyDecision[] }) {
  if (!items.length) return null;
  return (
    <ul className={t$.decisions} data-testid="policy-decisions">
      {items.map((d) => (
        <li key={d.rule} data-decision={d.decision}>
          <Chip tone={d.decision === "BLOCK" ? "bad" : d.decision === "ALLOW" ? "ok" : "plain"}>{d.decision}</Chip>
          <span>
            <b title={d.rule}>{RULE_LABELS[d.rule] ?? d.rule}</b>
            <em>{d.reason}</em>
          </span>
        </li>
      ))}
    </ul>
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
  return (
    <div className={t$.live} data-testid="live-status" data-terminal={status.terminal}>
      <span className={t$.ring} aria-hidden="true" />
      <span className={t$.liveText}>
        <b>{STATUS_LABEL[status.status] ?? status.status}</b>
        <code>{status.status}</code>
        {status.endedReason && <code>{status.endedReason}</code>}
      </span>
      {(status.terminal || gaveUp) && (
        <button
          type="button"
          className={ui.ghostBtn}
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

function PhoneIcon() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
      <path
        d="M6.6 3.5 9 3l2 4.6-1.9 1.3a12 12 0 0 0 6 6l1.3-1.9L21 15l-.5 2.4a2 2 0 0 1-2 1.6A16 16 0 0 1 5 5.5a2 2 0 0 1 1.6-2Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export default function TelephonyPage() {
  const { cap } = useCapabilities();
  const [accounts, setAccounts] = useState<Account[] | null>(null);
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
        .catch(() => setAccounts((x) => x ?? [])),
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
  const maxAttempts = Number(cap?.policy?.max_contact_attempts ?? 3);
  const stopped = contactPoints.filter((c) => c.stop_contact);

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
    <MotionRoot>
      <main className={t$.page}>
        <m.header className={t$.head} initial={{ opacity: 0, y: 20, filter: "blur(8px)" }} animate={{ opacity: 1, y: 0, filter: "blur(0px)" }} transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1] }}>
          <span className={ui.overline}>/telephony · PSTN via Twilio</span>
          <h1 className={ui.h1}>
            Real phone calls.
            <br />
            <span className={ui.grad}>Same rules.</span>
          </h1>
          <p className={ui.lead}>
            Phone calls run on the same controller, policy engine and audit trail as the browser demo. The backend checks the allowlist and the contact policy before
            anything is dialled.
          </p>
          <div className={ui.statusRow} style={{ justifyContent: "center" }} aria-label="Telephony status">
            <StatusChip label="Telephony" value={t ? (t.active ? "active" : "disabled") : "…"} tone={t?.active ? "ok" : "plain"} live={!!t?.active} />
            <StatusChip label="Operator endpoints" value={t ? (t.operator_endpoints ? "enabled" : "disabled") : "—"} tone={t?.operator_endpoints ? "ok" : "plain"} />
            <StatusChip label="Allow-listed numbers" value={String(t?.allowed_numbers_configured ?? 0)} />
            <StatusChip label="Transfer number" value={t?.transfer_number_configured ? "configured" : "simulated"} tone={t?.transfer_number_configured ? "ok" : "mock"} />
          </div>
        </m.header>

        <div className={t$.grid}>
          <form className={t$.card} onSubmit={onStart} aria-label="Start call">
            <h2 className={t$.cardTitle}>Start a demo call</h2>
            <label className={t$.field}>
              <span>Destination (E.164)</span>
              <input className={t$.input} type="tel" placeholder="+819012345678" value={to} onChange={(e) => setTo(e.target.value)} autoComplete="off" inputMode="tel" />
            </label>
            <p className={ui.fine}>Only allowlisted demo numbers can be dialed{t ? ` (${t.allowed_numbers_configured} configured on the backend)` : ""}.</p>
            {to && !normalised && (
              <p className={t$.invalid} role="alert">
                Enter the number in E.164 format, e.g. +819012345678.
              </p>
            )}
            <div className={t$.row}>
              <label className={t$.field}>
                <span>Scenario</span>
                <select className={t$.input} value={scenario} onChange={(e) => setScenario(e.target.value)} aria-label="Scenario">
                  {(scenarios.length ? scenarios : [{ key: "A", title: "" } as Scenario]).map((s) => (
                    <option key={s.key} value={s.key}>
                      {s.key}
                      {s.title ? ` — ${s.title}` : ""}
                    </option>
                  ))}
                </select>
              </label>
              <label className={t$.field}>
                <span>Language</span>
                <select className={t$.input} value={language} onChange={(e) => setLanguage(e.target.value)} aria-label="Language">
                  <option value="en">English</option>
                  <option value="ja">Japanese</option>
                </select>
              </label>
            </div>
            <Cta type="submit" disabled={busy || !normalised}>
              {busy ? <span className={t$.spinner} aria-hidden="true" /> : <PhoneIcon />}
              {busy ? "Starting call…" : "Start Call"}
            </Cta>
            <p className={ui.fine}>
              The call is placed server-to-server; the backend checks the allowlist and the contact policy (calling window, attempt limit, stop-contact) before dialling.
              Blocked calls are never placed.
            </p>
          </form>

          <section className={cx(t$.card, t$.result)} aria-live="polite" aria-label="Result">
            <h2 className={t$.cardTitle}>Result</h2>
            <AnimatePresence mode="wait" initial={false}>
              {!result && !busy && (
                <m.div key="idle" className={t$.idle} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
                  <span className={t$.idleIcon} aria-hidden="true">
                    <PhoneIcon />
                  </span>
                  <p>No call started yet.</p>
                </m.div>
              )}
              {busy && (
                <m.div key="busy" className={t$.idle} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
                  <span className={cx(t$.idleIcon, t$.idleBusy)} aria-hidden="true">
                    <PhoneIcon />
                  </span>
                  <p>Starting call…</p>
                </m.div>
              )}
              {result?.kind === "dialing" && (
                <m.div key="dialing" className={t$.outcome} data-testid="call-dialing" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={SPRING}>
                  <LiveStatus sessionId={result.sessionId} />
                  <dl className={t$.kv}>
                    <dt>Status</dt>
                    <dd>Dialing</dd>
                    <dt>Session</dt>
                    <dd data-testid="session-id">{result.sessionId}</dd>
                    <dt>Twilio call</dt>
                    <dd data-testid="call-id">{result.callId ?? "—"}</dd>
                  </dl>
                  <Decisions items={result.decisions} />
                  <Link className={ui.ghostBtn} href={`/sessions?id=${result.sessionId}`}>
                    Open Session
                  </Link>
                </m.div>
              )}
              {result?.kind === "blocked" && (
                <m.div key="blocked" className={t$.outcome} data-testid="call-blocked" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0, x: [0, -6, 6, -3, 0] }} transition={{ duration: 0.45 }}>
                  <div className={cx(ui.banner, ui.bannerBad)} role="alert">
                    Call blocked by policy — nothing was dialled.
                  </div>
                  <Decisions items={result.decisions} />
                </m.div>
              )}
              {result?.kind === "error" && (
                <m.div key="error" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={SPRING}>
                  <div className={cx(ui.banner, ui.bannerBad)} role="alert" data-testid="call-error" data-code={result.code}>
                    <span>
                      {result.message} {result.status ? <span className={ui.mono}>(HTTP {result.status})</span> : null}
                    </span>
                  </div>
                </m.div>
              )}
            </AnimatePresence>
            {!t?.active && (
              <p className={t$.setup}>
                Real phone calls need <code>TELEPHONY_ENABLED=true</code>, the <code>TWILIO_*</code> variables, <code>OPERATOR_TOKEN</code> and{" "}
                <code>DEMO_CALL_ALLOWED_NUMBERS</code> on the backend, and the same <code>OPERATOR_TOKEN</code> as a server-only variable on the frontend. The browser
                demo does not depend on any of this.
              </p>
            )}
          </section>
        </div>

        <section className={t$.card} data-testid="eligibility" aria-labelledby="elig">
          <div className={t$.eligHead}>
            <h2 id="elig" className={t$.cardTitle}>
              Contact eligibility
            </h2>
            <span className={ui.fine}>max {maxAttempts} attempts per account · simulated demo policy</span>
          </div>
          <p className={t$.eligNote}>
            A stop-contact request applies to the debtor (all of their accounts) and to the phone number where it was heard: any later call to that number is blocked for
            every scenario.
          </p>
          <ul className={t$.accounts}>
            {accounts === null &&
              Array.from({ length: 4 }, (_, i) => (
                <li key={i} className={t$.skel} aria-hidden="true">
                  <i />
                </li>
              ))}
            {(accounts ?? []).map((a) => {
              const eligible = a.eligible ?? !(a.stop_contact || a.debtor_stop_contact);
              const since = (a.debtor_stop_contact_at || a.stop_contact_at)?.slice(0, 16);
              return (
                <li key={a.scenario_key} data-testid={`account-${a.scenario_key}`} data-eligible={eligible}>
                  <em className={t$.key}>{a.scenario_key}</em>
                  <span className={t$.debtor}>
                    <b>{a.debtor_name ?? "—"}</b>
                    <small>synthetic debtor</small>
                  </span>
                  <span className={t$.attempts} title={`${a.contact_attempts} of ${maxAttempts} contact attempts used`}>
                    <span aria-hidden="true">
                      {Array.from({ length: maxAttempts }, (_, i) => (
                        <i key={i} data-used={i < a.contact_attempts} />
                      ))}
                    </span>
                    <small>
                      {a.contact_attempts}/{maxAttempts} attempts
                    </small>
                  </span>
                  <span className={t$.elig}>
                    {eligible ? (
                      <Chip tone="ok" dot>
                        eligible
                      </Chip>
                    ) : (
                      <Chip tone="bad" dot>
                        not eligible · {a.debtor_stop_contact ? "debtor" : "account"} stop-contact{since ? ` since ${since}` : ""}
                      </Chip>
                    )}
                  </span>
                </li>
              );
            })}
          </ul>
          <h3 className={t$.subTitle}>Phone numbers with a stop-contact request</h3>
          {stopped.length === 0 ? (
            <p className={t$.none} data-testid="contact-points-empty">
              None.
            </p>
          ) : (
            <ul className={t$.points} data-testid="contact-points">
              {stopped.map((c) => (
                <li key={c.label}>
                  <code>{c.label}</code>
                  <Chip tone="bad" dot>
                    blocked for every scenario
                  </Chip>
                  <small>since {c.stop_contact_at?.slice(0, 16) ?? "—"}</small>
                </li>
              ))}
            </ul>
          )}
        </section>
      </main>
    </MotionRoot>
  );
}
