"use client";

import { useEffect, useRef } from "react";
import { humanize, ms, yen } from "@/lib/format";
import {
  BargeIn,
  Collection,
  ConsoleState,
  LatencyItem,
  PolicyItem,
  TranscriptItem,
  WATERFALL,
  percentile,
  turnTotal,
} from "@/lib/session";

const STATES = ["IDLE", "LISTENING", "USER_SPEAKING", "PROCESSING", "AGENT_SPEAKING", "INTERRUPTED", "TRANSFER_REQUESTED", "ENDED", "ERROR"];

export function TranscriptPanel({ items, partial, lang }: { items: TranscriptItem[]; partial: string; lang: string }) {
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // Block body on purpose: Chrome 14x's scrollIntoView() returns a Promise, and an
    // expression-bodied effect would hand it to React as a "cleanup" -> route crash.
    try {
      void end.current?.scrollIntoView({ block: "nearest" });
    } catch {
      /* autoscroll is cosmetic */
    }
  }, [items.length, partial]);
  return (
    <div className="panel">
      <div className="panel-head">
        <h3>Conversation</h3>
        <span className="muted small">{lang === "ja" ? "日本語" : "English"}</span>
      </div>
      <div className="transcript" aria-live="polite">
        {items.length === 0 && <div className="muted small">Waiting for the agent…</div>}
        {items.map((t) => (
          <div key={t.key} className={`msg ${t.speaker} ${t.interrupted ? "interrupted" : ""}`}>
            {t.interrupted && t.spokenText !== undefined ? (
              <>
                <span>{t.spokenText}</span> <span className="cut">{t.text.slice((t.spokenText ?? "").length)}</span>
              </>
            ) : (
              t.text
            )}
            <div className="meta">
              {t.speaker === "agent" ? `agent · ${t.acts ?? ""}${t.realizer && t.realizer !== "template" ? ` · ${t.realizer}` : ""}` : `caller · ${(t.intents ?? []).join(", ")}`}
              {t.interrupted && " · interrupted (barge-in)"}
            </div>
          </div>
        ))}
        {partial && <div className="msg caller muted">{partial}…</div>}
        <div ref={end} />
      </div>
    </div>
  );
}

export function AgentStatePanel({ s }: { s: ConsoleState }) {
  const last = s.lifecycle.slice(-6).reverse();
  return (
    <div className="panel">
      <div className="panel-head">
        <h3>Agent state</h3>
        <span className="pill info mono">{s.voiceState}</span>
      </div>
      <div className="state-machine" style={{ marginBottom: 10 }}>
        {STATES.map((x) => (
          <span key={x} className={`st ${s.voiceState === x ? "on" : ""}`}>
            {x}
          </span>
        ))}
      </div>
      <div className="small mono muted">
        {last.map((t, i) => (
          <div key={i}>
            {t.from} → {t.to} <span style={{ opacity: 0.7 }}>({t.cause})</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function Flag({ on, yes, no, bad }: { on: boolean; yes: string; no: string; bad?: boolean }) {
  return <span className={`pill ${on ? (bad ? "bad" : "ok") : "muted"}`}>{on ? yes : no}</span>;
}

export function CollectionPanel({ c, lang }: { c?: Collection; lang: "en" | "ja" }) {
  if (!c) return <div className="panel muted small">Collection state appears when the session starts.</div>;
  const promiseCls = c.promise_status === "CONFIRMED" ? "ok" : c.promise_status === "REJECTED" ? "bad" : c.promise_status === "PENDING_CONFIRMATION" ? "warn" : "muted";
  return (
    <div className="panel">
      <div className="panel-head">
        <h3>Collection state (authoritative)</h3>
        <span className="pill muted mono">{c.phase}</span>
      </div>
      <dl className="kv">
        <dt>Identity</dt>
        <dd>
          <span className={`pill ${c.identity_status === "VERIFIED" ? "ok" : c.identity_status === "UNVERIFIED" || c.identity_status === "NAME_CONFIRMED" ? "warn" : "bad"}`}>{c.identity_status}</span>
          {c.identity_attempts > 0 && ` · ${c.identity_attempts} failed`}
        </dd>
        <dt>Disclosure</dt>
        <dd>
          <Flag on={c.disclosure_allowed} yes="allowed" no="blocked" /> {c.debt_disclosed && <span className="pill info">disclosed</span>}
        </dd>
        <dt>Balance</dt>
        <dd>{c.disclosure_allowed ? yen(c.outstanding_balance, lang) : <span className="muted">withheld until verified</span>}</dd>
        <dt>Approved envelope</dt>
        <dd>
          min {yen(c.allowed_min_payment, lang)} · ≤ {c.max_extension_days} days
        </dd>
        <dt>Proposal</dt>
        <dd>
          {c.proposed_amount !== null || c.proposed_date ? `${yen(c.proposed_amount, lang)} on ${c.proposed_date ?? "?"}` : "—"}
          {c.promise_status === "PENDING_CONFIRMATION" && <span className="muted"> · read-back {c.readback_delivered ? "heard" : "not yet heard"}</span>}
        </dd>
        <dt>Promise-to-pay</dt>
        <dd>
          <span className={`pill ${promiseCls}`}>{c.promise_status}</span>
        </dd>
        {c.promise && (
          <>
            <dt>Promise</dt>
            <dd>
              {yen(c.promise.amount, lang)} due {c.promise.due_date} · turn {c.promise.confirmation_turn} · {c.promise.policy_decision_ids.length} policy checks
            </dd>
          </>
        )}
        <dt>Stop-contact</dt>
        <dd>
          <Flag on={c.stop_contact} yes="active" no="no" bad /> future contact: {c.future_contact_eligible ? "eligible" : "disabled"}
        </dd>
        <dt>Human transfer</dt>
        <dd>
          <Flag on={c.human_transfer_requested} yes={c.transfer_status} no="not requested" /> {c.transfer_reason && <span className="muted">{humanize(c.transfer_reason)}</span>}
        </dd>
        <dt>Call status</dt>
        <dd>
          {c.call_status}
          {c.ended_reason && ` · ${c.ended_reason}`}
        </dd>
      </dl>
    </div>
  );
}

export function PolicyPanel({ items }: { items: PolicyItem[] }) {
  const rows = [...items].reverse();
  const blocks = items.filter((p) => p.decision === "BLOCK").length;
  return (
    <div className="panel">
      <div className="panel-head">
        <h3>Policy decisions</h3>
        <span className="small muted">
          {items.length} total · {blocks} blocked
        </span>
      </div>
      <div className="feed">
        {rows.length === 0 && <div className="muted small">No decisions yet.</div>}
        {rows.map((p) => (
          <div className="feed-row" key={p.decision_id}>
            <span className={`pill ${p.decision === "BLOCK" ? "bad" : p.decision === "ALLOW" ? "ok" : "muted"}`}>{p.decision === "NOT_APPLICABLE" ? "N/A" : p.decision}</span>
            <div>
              <div className="rule">{p.rule}</div>
              <div className="reason">{p.reason}</div>
            </div>
          </div>
        ))}
      </div>
      <p className="small muted" style={{ marginTop: 8, marginBottom: 0 }}>
        Simulated demo policy inspired by regulated collections workflows — not legal requirements.
      </p>
    </div>
  );
}

const COLORS = ["#8aa4d6", "#c9a0dc", "#e7b36a", "#6cc0a8", "#e58e8e", "#7fb2e5"];

export function LatencyPanel({ items, bargeIns, providers }: { items: LatencyItem[]; bargeIns: BargeIn[]; providers?: Record<string, string> }) {
  const turns = items.filter((l) => l.input_mode !== "system" && l.input_mode !== "silence");
  const totals = turns.map((t) => turnTotal(t.stages)).filter((v): v is number => v !== undefined);
  const scale = Math.max(1500, ...totals) * 1.05;
  const mock = !providers || Object.entries(providers).some(([k, v]) => ["stt", "tts", "llm"].includes(k) && (v.startsWith("mock") || v === "fake"));
  return (
    <div className="panel">
      <div className="panel-head">
        <h3>Latency waterfall</h3>
        <span className="small muted">
          p50 {ms(percentile(totals, 50))} · p95 {ms(percentile(totals, 95))} · n={totals.length}
        </span>
      </div>
      {mock && (
        <p className="small muted">
          Mock providers in use: these timings measure this pipeline only (end-of-turn wait, policy, playback start) and are not real speech-provider latency.
        </p>
      )}
      <div className="wf">
        {turns.length === 0 && <div className="muted small">Measured per turn once you speak or type.</div>}
        {turns.slice(-8).map((t) => {
          const total = turnTotal(t.stages) ?? 0;
          let x = 0;
          return (
            <div className="wf-row" key={t.turn_index + t.input_mode}>
              <span className="mono muted">
                #{t.turn_index} {t.input_mode === "voice" ? "🎙" : "⌨"}
              </span>
              <div className="wf-bar" title={JSON.stringify(t.stages)}>
                {WATERFALL.map((w, i) => {
                  const v = t.stages[w.key];
                  if (!v) return null;
                  const seg = (
                    <div key={w.key} className="wf-seg" style={{ left: `${(x / scale) * 100}%`, width: `${Math.max(0.4, (v / scale) * 100)}%`, background: COLORS[i] }} />
                  );
                  x += v;
                  return seg;
                })}
                <div className="wf-budget" style={{ left: `${(1500 / scale) * 100}%` }} title="1.5 s budget" />
              </div>
              <span className={`mono ${total > 1500 ? "" : "muted"}`} style={total > 1500 ? { color: "var(--bad)" } : undefined}>
                {ms(total)}
              </span>
            </div>
          );
        })}
      </div>
      <div className="legend" style={{ marginTop: 8 }}>
        {WATERFALL.map((w, i) => (
          <span key={w.key}>
            <i style={{ background: COLORS[i] }} />
            {w.label}
          </span>
        ))}
        <span>
          <i style={{ background: "var(--bad)" }} />
          1.5 s budget
        </span>
      </div>
      {bargeIns.length > 0 && (
        <div className="small" style={{ marginTop: 10 }}>
          <b>Barge-in</b>:{" "}
          {bargeIns.map((b, i) => (
            <span key={i} className="pill info mono" style={{ marginRight: 4 }}>
              detect→stopped {b.cancel_latency_ms.toFixed(1)} ms · {b.source}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
