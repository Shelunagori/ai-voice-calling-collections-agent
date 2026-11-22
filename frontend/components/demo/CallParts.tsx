"use client";

// Live-call building blocks for /demo. Every value shown comes from the session reducer (lib/session.ts), i.e. from
// server events; nothing here is scripted. Presentation-only choices (word reveal, flash) are noted where they occur.
import { AnimatePresence, m } from "framer-motion";
import { Fragment, useEffect, useRef } from "react";
import { cx } from "@/components/ui/cx";
import { SPRING } from "@/components/ui/motion";
import ui from "@/components/ui/ui.module.css";
import { humanize, ms, yen } from "@/lib/format";
import {
  type BargeIn,
  type Collection,
  type LatencyItem,
  type LifecycleItem,
  type PolicyItem,
  type TranscriptItem,
  WATERFALL,
  percentile,
  turnTotal,
} from "@/lib/session";
import d from "./demo.module.css";

// Readable tags for non-default realizer sources shown on agent turns.
const REALIZER_LABEL: Record<string, string> = { resume_after_noise: "repeated after noise" };

type Lang = "en" | "ja";
export type RawBargeIn = BargeIn & { barge_in_detected_at?: number };

export function fmtClock(ms: number) {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

/** "2026-12-06" -> "Sun, Dec 6" (UTC, so it never shifts a day in the viewer's time zone). */
export function fmtDue(iso: string | null | undefined, lang: Lang) {
  if (!iso) return "—";
  const dt = new Date(`${iso}T00:00:00Z`);
  if (Number.isNaN(dt.getTime())) return iso;
  return dt.toLocaleDateString(lang === "ja" ? "ja-JP" : "en-US", { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" });
}

/* ------------------------------------------------------------ transcript */

function Words({ text, animate }: { text: string; animate: boolean }) {
  const words = text.split(/(\s+)/).filter(Boolean);
  if (!animate) return <>{text}</>;
  // Presentation only: the server sends each reply's full text when the agent starts speaking; words blur in
  // quickly so the reply reads like speech. It is not claimed to be synchronised with audio.
  let n = 0;
  return (
    <>
      {words.map((w, i) => {
        if (/^\s+$/.test(w)) return w;
        const delay = Math.min(n++ * 0.035, 1.4);
        return (
          <m.span key={i} className={d.word} initial={{ opacity: 0, filter: "blur(6px)", y: 3 }} animate={{ opacity: 1, filter: "blur(0px)", y: 0 }} transition={{ duration: 0.35, delay, ease: "easeOut" }}>
            {w}
          </m.span>
        );
      })}
    </>
  );
}

export function LiveTranscript({
  items,
  partial,
  speakingKey,
  bargeIns,
  empty,
}: {
  items: TranscriptItem[];
  partial: string;
  speakingKey: string | null;
  bargeIns: RawBargeIn[];
  empty: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const lastText = items[items.length - 1]?.text;
  useEffect(() => {
    const el = box.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [items.length, partial, lastText]);
  // barge-ins are reported per agent turn; show the divider under the interrupted agent bubble
  let bargeIdx = 0;
  return (
    <div className={d.transcript} ref={box} aria-live="polite" aria-label="Conversation transcript">
      {items.length === 0 && (
        <div className={d.skelRow}>
          <span className={d.skel} />
          <span className={d.skelText}>{empty}</span>
        </div>
      )}
      {items.map((t) => {
        // framer's `initial` only runs on mount; items keep their key when the server finalises them, so a
        // finished or interrupted reply never re-animates.
        const cut = t.speaker === "agent" && t.interrupted;
        const spoken = cut ? (t.spokenText ?? "") : t.text;
        const rest = cut ? t.text.slice(spoken.length) : "";
        const b = cut ? bargeIns[bargeIdx++] : undefined;
        return (
          <Fragment key={t.key}>
            <m.div
              className={cx(d.msg, t.speaker === "agent" ? d.msgAgent : d.msgCaller)}
              initial={{ opacity: 0, y: 14, scale: 0.97 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              transition={SPRING}
            >
              <div className={d.who}>
                <i />
                {t.speaker === "agent" ? "Agent" : "Caller"}
                {t.speaker === "agent" && t.realizer && t.realizer !== "template" && <span className={d.lat}>{REALIZER_LABEL[t.realizer] ?? t.realizer}</span>}
              </div>
              <div className={cx(d.bubble, cut && d.bubbleCut)}>
                <Words text={spoken} animate={t.speaker === "agent"} />
                {rest && <span className={d.cut}>{rest}</span>}
                {speakingKey === t.key && <span className={d.caret} aria-hidden="true" />}
              </div>
              {t.speaker === "agent" && t.acts && <div className={d.tag}>{t.acts}</div>}
              {t.speaker === "caller" && (t.intents?.length ?? 0) > 0 && (
                <div className={d.intents}>
                  {t.intents!.map((x, i) => (
                    <span key={i}>{x}</span>
                  ))}
                </div>
              )}
            </m.div>
            {cut && (
              <m.div
                className={d.bargeLine}
                initial={{ opacity: 0, scaleX: 0.3 }}
                animate={{ opacity: 1, scaleX: 1, x: [0, -6, 6, -3, 0] }}
                transition={{ duration: 0.45 }}
                role="note"
              >
                <span>
                  <b>BARGE-IN</b> {b ? `agent audio stopped in ${b.cancel_latency_ms.toFixed(1)} ms · ${humanize(b.source).toLowerCase()}` : "caller interrupted"}
                </span>
              </m.div>
            )}
          </Fragment>
        );
      })}
      {partial && (
        <div className={cx(d.msg, d.msgCaller, d.partial)}>
          <div className={d.who}>
            <i />
            Caller · hearing…
          </div>
          <div className={d.bubble}>{partial}…</div>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------ timeline */

const SEG_CLASS: Record<string, string | undefined> = { AGENT_SPEAKING: d.segAgent, USER_SPEAKING: d.segCaller, PROCESSING: d.segThink, TRANSFER_REQUESTED: d.segThink };

/** Call timeline from server lifecycle timestamps (server monotonic clock) with barge-in markers. */
export function Timeline({ lifecycle, bargeIns, nowServer, elapsedMs }: { lifecycle: LifecycleItem[]; bargeIns: RawBargeIn[]; nowServer: number | null; elapsedMs: number }) {
  if (lifecycle.length === 0) return <div className={d.timeline} aria-hidden="true"><div className={d.track} /></div>;
  const t0 = lifecycle[0].at;
  const tEnd = Math.max(nowServer ?? lifecycle[lifecycle.length - 1].at, lifecycle[lifecycle.length - 1].at, t0 + 1);
  const span = tEnd - t0;
  const pct = (t: number) => `${Math.min(100, Math.max(0, ((t - t0) / span) * 100))}%`;
  const live = lifecycle[lifecycle.length - 1].to !== "ENDED";
  return (
    <div className={d.timeline} aria-label="Call timeline">
      <div className={d.track}>
        {lifecycle.map((l, i) => {
          const cls = SEG_CLASS[l.to];
          if (!cls) return null;
          const end = lifecycle[i + 1]?.at ?? tEnd;
          return <span key={i} className={cls} style={{ left: pct(l.at), width: `max(2px, ${((end - l.at) / span) * 100}%)` }} />;
        })}
        {bargeIns.map((b, i) =>
          b.barge_in_detected_at ? (
            <m.span
              key={i}
              className={d.bargeMark}
              data-edge={(b.barge_in_detected_at - t0) / span > 0.7 ? "end" : (b.barge_in_detected_at - t0) / span < 0.3 ? "start" : undefined}
              style={{ left: pct(b.barge_in_detected_at) }} initial={{ scale: 0, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={{ type: "spring", stiffness: 400, damping: 14 }}>
              {i === bargeIns.length - 1 && <em>barge-in · {b.cancel_latency_ms.toFixed(1)} ms</em>}
            </m.span>
          ) : null,
        )}
        {live && <span className={d.playhead} style={{ left: "100%" }} />}
      </div>
      <div className={d.trackLegend}>
        <span>
          <i className={d.segAgent} /> agent
        </span>
        <span>
          <i className={d.segCaller} /> caller
        </span>
        <span>
          <i className={d.segThink} /> thinking
        </span>
        <span className={d.grow} />
        <span className={ui.mono}>{fmtClock(elapsedMs)}</span>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------ promise card */

const BURST = Array.from({ length: 14 }, (_, i) => {
  const a = (i / 14) * Math.PI * 2;
  return { x: Math.round(Math.cos(a) * (60 + (i % 3) * 18)), y: Math.round(Math.sin(a) * (40 + (i % 2) * 16)) };
});

function LockIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
      <path d="M7 11V8a5 5 0 0 1 10 0v3M5 11h14v10H5z" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
    </svg>
  );
}

export function PromiseCard({ c, lang }: { c?: Collection; lang: Lang }) {
  const confirmed = c?.promise_status === "CONFIRMED" && !!c.promise;
  const pending = c?.promise_status === "PENDING_CONFIRMATION";
  const locked = !confirmed && !pending;
  let lockText = "Locked until identity is verified";
  let lockTone: "muted" | "bad" | "ok" = "muted";
  if (c) {
    if (c.identity_status === "VERIFIED") {
      lockText = c.promise_status === "REJECTED" ? "Proposal declined — waiting for a new offer" : `Identity verified — waiting for an offer ≥ ${yen(c.allowed_min_payment, lang)} within ${c.max_extension_days} days`;
      lockTone = "ok";
    } else if (c.identity_status !== "UNVERIFIED" && c.identity_status !== "NAME_CONFIRMED") {
      lockText = `Identity ${humanize(c.identity_status).toLowerCase()} — nothing can be disclosed or agreed`;
      lockTone = "bad";
    }
  }
  const amount = confirmed ? c!.promise!.amount : c?.proposed_amount;
  const due = confirmed ? c!.promise!.due_date : c?.proposed_date;
  return (
    <AnimatePresence mode="popLayout" initial={false}>
      {locked ? (
        <m.div key="locked" className={cx(d.promise, d.promiseLocked)} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0, scale: 0.96 }} data-tone={lockTone}>
          <div className={ui.overline}>Promise to pay</div>
          <div className={d.lockRow}>
            <LockIcon />
            {lockText}
          </div>
        </m.div>
      ) : (
        <m.div
          key="live"
          className={cx(d.promise, confirmed && d.promiseDone)}
          initial={{ opacity: 0, y: 30, scale: 0.92 }}
          animate={{ opacity: 1, y: 0, scale: confirmed ? [1, 1.035, 1] : 1 }}
          transition={{ ...SPRING, scale: { duration: 0.5 } }}
          role="status"
          aria-label={confirmed ? "Promise to pay confirmed" : "Promise to pay pending confirmation"}
        >
          <span className={d.promiseBorder} aria-hidden="true" />
          {confirmed && (
            <span className={d.burst} aria-hidden="true">
              {BURST.map((b, i) => (
                <m.i key={i} initial={{ x: 0, y: 0, opacity: 1, scale: 1 }} animate={{ x: b.x, y: b.y, opacity: 0, scale: 0.4 }} transition={{ duration: 0.9, ease: "easeOut" }} />
              ))}
            </span>
          )}
          <div className={d.promiseTop}>
            <div>
              <div className={ui.overline}>Promise to pay</div>
              <div className={d.amount}>{yen(amount ?? null, lang)}</div>
              <div className={d.dim}>due {fmtDue(due, lang)}</div>
            </div>
            <div className={d.check} aria-hidden="true">
              <svg viewBox="0 0 52 52" width="52" height="52">
                <circle cx="26" cy="26" r="23" className={d.checkTrack} />
                <m.circle
                  cx="26"
                  cy="26"
                  r="23"
                  className={d.checkRing}
                  initial={{ pathLength: 0 }}
                  animate={{ pathLength: confirmed ? 1 : c?.readback_delivered ? 0.66 : 0.33 }}
                  transition={{ duration: 0.8, ease: [0.22, 1, 0.36, 1] }}
                />
                {confirmed && <m.path d="M16 27l7 7 14-15" className={d.checkMark} initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ delay: 0.35, duration: 0.45 }} />}
              </svg>
            </div>
          </div>
          <div className={d.promiseFoot}>
            <span className={d.statusDot} />
            {confirmed
              ? `Confirmed after an explicit yes · ${c!.promise!.policy_decision_ids.length} policy checks`
              : c?.readback_delivered
                ? "Read-back heard · awaiting an explicit yes"
                : "Reading the terms back to the caller…"}
          </div>
        </m.div>
      )}
    </AnimatePresence>
  );
}

/* ------------------------------------------------------------ policy stream */

export function PolicyStream({ items }: { items: PolicyItem[] }) {
  const rows = items.slice(-4).reverse();
  const allow = items.filter((p) => p.decision === "ALLOW").length;
  const block = items.filter((p) => p.decision === "BLOCK").length;
  return (
    <div className={d.policy}>
      <div className={d.policyHead}>
        <span className={ui.overline}>Policy engine</span>
        <span className={d.dim}>
          {allow} allow · <span className={block ? d.blockCount : undefined}>{block} block</span>
        </span>
      </div>
      {rows.length === 0 && <div className={d.dim}>No decisions yet.</div>}
      <AnimatePresence initial={false}>
        {rows.map((p) => (
          <m.div key={p.decision_id} className={d.rule} data-decision={p.decision} layout initial={{ opacity: 0, x: 24 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }} transition={SPRING}>
            <b>{p.decision === "NOT_APPLICABLE" ? "N/A" : p.decision}</b>
            <span>
              <code>{p.rule}</code>
              <em>{p.reason}</em>
            </span>
          </m.div>
        ))}
      </AnimatePresence>
      <p className={ui.fine} style={{ marginTop: 8 }}>
        Simulated demo policy inspired by regulated collections workflows — not legal requirements.
      </p>
    </div>
  );
}

/* ------------------------------------------------------------ end-of-call summary */

export function EndSummary({
  reason,
  c,
  lang,
  elapsedMs,
  policy,
  bargeIns,
  latency,
  turns,
  actions,
}: {
  reason?: string;
  c?: Collection;
  lang: Lang;
  elapsedMs: number;
  policy: PolicyItem[];
  bargeIns: BargeIn[];
  latency: LatencyItem[];
  turns: number;
  actions: React.ReactNode;
}) {
  const promised = c?.promise_status === "CONFIRMED" && c.promise;
  const title = promised
    ? `Promise confirmed · ${yen(c!.promise!.amount, lang)}`
    : c?.stop_contact
      ? "Stop-contact recorded"
      : c?.human_transfer_requested
        ? `Transfer ${humanize(c.transfer_status).toLowerCase()}`
        : reason
          ? humanize(reason)
          : "Call ended";
  const totals = latency.filter((l) => l.input_mode !== "system" && l.input_mode !== "silence").map((l) => turnTotal(l.stages)).filter((v): v is number => v !== undefined);
  const block = policy.filter((p) => p.decision === "BLOCK").length;
  const fastest = bargeIns.length ? Math.min(...bargeIns.map((b) => b.cancel_latency_ms)) : null;
  return (
    <m.div className={cx(d.summary, promised && d.summaryOk)} initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={SPRING} role="region" aria-label="End-of-call summary">
      <div>
        <div className={ui.overline}>Call ended · {reason ? humanize(reason).toLowerCase() : "closed"}</div>
        <div className={d.summaryTitle}>{title}</div>
      </div>
      <dl>
        <div>
          <dt>Duration</dt>
          <dd>{fmtClock(elapsedMs)}</dd>
        </div>
        <div>
          <dt>Turns</dt>
          <dd>{turns}</dd>
        </div>
        <div>
          <dt>Policy</dt>
          <dd>
            {policy.length - block} / {block}
          </dd>
        </div>
        <div>
          <dt>Fastest barge-in</dt>
          <dd>{fastest === null ? "—" : `${fastest.toFixed(1)} ms`}</dd>
        </div>
        <div>
          <dt>Turn p50</dt>
          <dd>{ms(percentile(totals, 50))}</dd>
        </div>
      </dl>
      <div className={d.summaryBtns}>{actions}</div>
    </m.div>
  );
}

/* ------------------------------------------------------------ under the hood */

const STATES = ["IDLE", "LISTENING", "USER_SPEAKING", "PROCESSING", "AGENT_SPEAKING", "INTERRUPTED", "TRANSFER_REQUESTED", "ENDED", "ERROR"];
const WF_COLORS = ["var(--caller)", "var(--a1)", "var(--think)", "var(--listen)", "var(--agent-2)", "var(--agent)"];

export function UnderTheHood({
  c,
  lang,
  voiceState,
  lifecycle,
  latency,
  providers,
  notifications,
}: {
  c?: Collection;
  lang: Lang;
  voiceState: string;
  lifecycle: LifecycleItem[];
  latency: LatencyItem[];
  providers?: Record<string, string>;
  notifications: { channel: string; provider: string; delivered: boolean; detail: string }[];
}) {
  const turns = latency.filter((l) => l.input_mode !== "system" && l.input_mode !== "silence");
  const totals = turns.map((t) => turnTotal(t.stages)).filter((v): v is number => v !== undefined);
  const scale = Math.max(1500, ...totals) * 1.05;
  const mock = !providers || Object.entries(providers).some(([k, v]) => ["stt", "tts", "llm"].includes(k) && (v.startsWith("mock") || v === "fake"));
  return (
    <details className={d.hood}>
      <summary>
        <span className={ui.overline}>Under the hood</span>
        <span className={d.dim}>authoritative state · lifecycle · latency</span>
      </summary>
      <div className={d.hoodGrid}>
        <section className={d.hoodCard}>
          <h3 className={d.hoodTitle}>Collection state <span className={d.dim}>authoritative</span></h3>
          {!c ? (
            <p className={d.dim}>Appears when the session starts.</p>
          ) : (
            <dl className={d.kv}>
              <dt>Phase</dt>
              <dd>{c.phase}</dd>
              <dt>Identity</dt>
              <dd>
                {c.identity_status}
                {c.identity_attempts > 0 && ` · ${c.identity_attempts} failed`}
              </dd>
              <dt>Disclosure</dt>
              <dd>
                {c.disclosure_allowed ? "allowed" : "blocked"}
                {c.debt_disclosed && " · disclosed"}
              </dd>
              <dt>Balance</dt>
              <dd>{c.disclosure_allowed ? yen(c.outstanding_balance, lang) : "withheld until verified"}</dd>
              <dt>Envelope</dt>
              <dd>
                min {yen(c.allowed_min_payment, lang)} · ≤ {c.max_extension_days} days
              </dd>
              <dt>Proposal</dt>
              <dd>{c.proposed_amount !== null || c.proposed_date ? `${yen(c.proposed_amount, lang)} on ${c.proposed_date ?? "?"}` : "—"}</dd>
              <dt>Promise</dt>
              <dd>{c.promise_status}</dd>
              <dt>Stop-contact</dt>
              <dd>
                {c.stop_contact ? "active" : "no"} · future contact {c.future_contact_eligible ? "eligible" : "disabled"}
              </dd>
              <dt>Transfer</dt>
              <dd>
                {c.human_transfer_requested ? c.transfer_status : "not requested"}
                {c.transfer_reason && ` · ${humanize(c.transfer_reason)}`}
              </dd>
              <dt>Call</dt>
              <dd>
                {c.call_status}
                {c.ended_reason && ` · ${c.ended_reason}`}
              </dd>
            </dl>
          )}
        </section>
        <section className={d.hoodCard}>
          <h3 className={d.hoodTitle}>Voice lifecycle</h3>
          <div className={d.states}>
            {STATES.map((x) => (
              <span key={x} data-on={voiceState === x}>
                {x}
              </span>
            ))}
          </div>
          <div className={d.transitions}>
            {lifecycle
              .slice(-6)
              .reverse()
              .map((t, i) => (
                <div key={i}>
                  {t.from} → {t.to} <span className={d.dim}>({t.cause})</span>
                </div>
              ))}
          </div>
        </section>
        <section className={cx(d.hoodCard, d.hoodWide)}>
          <h3 className={d.hoodTitle}>
            Latency waterfall
            <span className={d.dim}>
              p50 {ms(percentile(totals, 50))} · p95 {ms(percentile(totals, 95))} · n={totals.length}
            </span>
          </h3>
          {mock && <p className={ui.fine}>Mock providers in use: these timings measure this pipeline only (end-of-turn wait, policy, playback start), not real speech-provider latency.</p>}
          <div className={d.wf}>
            {turns.length === 0 && <div className={d.dim}>Measured per turn once you speak or type.</div>}
            {turns.slice(-8).map((t) => {
              const total = turnTotal(t.stages) ?? 0;
              let x = 0;
              return (
                <div className={d.wfRow} key={t.turn_index + t.input_mode}>
                  <span className={ui.mono}>
                    #{t.turn_index} {t.input_mode === "voice" ? "voice" : "typed"}
                  </span>
                  <div className={d.wfBar}>
                    {WATERFALL.map((w, i) => {
                      const v = t.stages[w.key];
                      if (!v) return null;
                      const seg = <i key={w.key} style={{ left: `${(x / scale) * 100}%`, width: `${Math.max(0.6, (v / scale) * 100)}%`, background: WF_COLORS[i] }} title={`${w.label}: ${ms(v)}`} />;
                      x += v;
                      return seg;
                    })}
                    <b style={{ left: `${(1500 / scale) * 100}%` }} title="1.5 s budget" />
                  </div>
                  <span className={cx(ui.mono, total > 1500 && d.over)}>{ms(total)}</span>
                </div>
              );
            })}
          </div>
          <div className={d.legend}>
            {WATERFALL.map((w, i) => (
              <span key={w.key}>
                <i style={{ background: WF_COLORS[i] }} />
                {w.label}
              </span>
            ))}
            <span>
              <i style={{ background: "var(--bad)" }} />
              1.5 s budget
            </span>
          </div>
          {notifications.length > 0 && (
            <div className={d.notes}>
              {notifications.map((n, i) => (
                <div key={i}>
                  {n.channel.toUpperCase()} via {n.provider}: {n.detail}
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </details>
  );
}
