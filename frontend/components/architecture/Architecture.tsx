"use client";

import { m } from "framer-motion";
import { useState } from "react";
import { Chip } from "@/components/ui/Chip";
import { cx } from "@/components/ui/cx";
import { MotionRoot, SPRING } from "@/components/ui/motion";
import ui from "@/components/ui/ui.module.css";
import a from "./arch.module.css";

type Kind = "llm" | "auth" | undefined;
type Node = { x: number; y: number; w?: number; t: string; s: string; kind?: Kind };

const N: Record<string, Node> = {
  caller: { x: 10, y: 20, t: "Caller", s: "browser mic / PSTN" },
  transport: { x: 200, y: 20, t: "Transport", s: "WebSocket · Twilio Media" },
  vad: { x: 390, y: 20, t: "VAD", s: "energy + noise floor" },
  stt: { x: 580, y: 20, t: "Streaming STT", s: "Cartesia Ink / mock" },
  eot: { x: 770, y: 20, w: 160, t: "End-of-turn", s: "semantic silence rules" },
  barge: { x: 390, y: 120, t: "Barge-in", s: "gen bump · cancel · clear" },
  nlu: { x: 770, y: 120, w: 160, t: "Understanding", s: "LLM JSON + rules net", kind: "llm" },
  play: { x: 200, y: 220, t: "Paced playback", s: "generation-tagged" },
  tts: { x: 390, y: 220, t: "Streaming TTS", s: "Cartesia Sonic / mock" },
  real: { x: 580, y: 220, t: "Realizer + guard", s: "templates / LLM rephrase", kind: "llm" },
  ctrl: { x: 770, y: 220, w: 160, t: "Controller.apply()", s: "authoritative state", kind: "auth" },
  audit: { x: 580, y: 320, t: "Audit + DB", s: "PostgreSQL" },
  policy: { x: 770, y: 320, w: 160, t: "Policy engine", s: "deterministic, audited", kind: "auth" },
};

// Edges are drawn source → target so the flow particles travel in the real direction of data.
type Edge = { d: string; tone: "audio" | "control" | "auth"; dashed?: boolean; both?: boolean; label?: [number, number, string] };
const EDGES: Edge[] = [
  { d: "M160 45 H200", tone: "audio", both: true },
  { d: "M350 45 H390", tone: "audio" },
  { d: "M540 45 H580", tone: "audio" },
  { d: "M730 45 H770", tone: "audio" },
  { d: "M850 70 V120", tone: "audio", label: [858, 100, "turn text"] },
  { d: "M850 170 V220", tone: "auth", label: [858, 200, "typed proposal"] },
  { d: "M770 245 H730", tone: "audio" },
  { d: "M580 245 H540", tone: "audio" },
  { d: "M390 245 H350", tone: "audio" },
  { d: "M275 220 V70", tone: "audio", label: [284, 150, "audio out"] },
  { d: "M850 270 V320", tone: "auth", both: true, label: [858, 300, "check"] },
  { d: "M780 270 L700 320", tone: "auth", dashed: true, label: [640, 300, "every decision"] },
  { d: "M465 70 V120", tone: "control" },
  { d: "M430 170 L330 220", tone: "control", dashed: true },
  { d: "M480 170 V220", tone: "control", dashed: true, label: [488, 200, "cancel"] },
];

function Box({ n, focus }: { n: Node; focus: "all" | "llm" | "auth" }) {
  const w = n.w ?? 150;
  const dim = focus !== "all" && n.kind !== focus;
  return (
    <g className={a.node} data-kind={n.kind ?? "plain"} data-dim={dim}>
      <rect x={n.x} y={n.y} width={w} height={50} rx={12} />
      <text className={a.lbl} x={n.x + w / 2} y={n.y + 22} textAnchor="middle">
        {n.t}
      </text>
      <text className={a.sub} x={n.x + w / 2} y={n.y + 38} textAnchor="middle">
        {n.s}
      </text>
    </g>
  );
}

function Pipeline({ focus }: { focus: "all" | "llm" | "auth" }) {
  return (
    <svg className={a.diagram} viewBox="0 0 975 390" role="img" aria-label="Audio-to-audio pipeline: caller audio flows through transport, VAD, streaming STT and end-of-turn detection to understanding (language model), then a typed proposal goes to the controller, which checks the policy engine and writes every decision to the audit log; replies go through the realizer, streaming TTS and paced playback back to the caller. Barge-in cancels TTS and playback.">
      <defs>
        <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0,0 L10,5 L0,10 z" className={a.arrowHead} />
        </marker>
      </defs>
      {EDGES.map((e, i) => (
        <g key={i} className={a.edge} data-tone={e.tone} data-dim={focus === "llm" ? true : focus === "auth" && e.tone !== "auth"}>
          <path className={cx(a.edgeBase, e.dashed && a.dashed)} d={e.d} markerEnd="url(#arr)" markerStart={e.both ? "url(#arr)" : undefined} />
          <path className={a.flow} d={e.d} style={{ animationDelay: `${(i % 5) * -0.37}s` }} />
          {e.label && (
            <text className={a.edgeLbl} x={e.label[0]} y={e.label[1]}>
              {e.label[2]}
            </text>
          )}
        </g>
      ))}
      {Object.entries(N).map(([k, n]) => (
        <Box key={k} n={n} focus={focus} />
      ))}
    </svg>
  );
}

const ROWS: [string, string, string][] = [
  ["Conversation controller", "Implemented", "Owns CollectionState; applies typed proposals only after policy checks."],
  ["Policy engine", "Implemented", "Identity-before-disclosure, envelope (min / max days / ≤ balance), no discounts, stop-contact, transfer, calling hours, attempt limits."],
  ["Promise-to-pay", "Implemented", "Requires verified identity, valid terms, a fully played read-back and an explicit yes; one per session (DB unique)."],
  ["Barge-in", "Implemented", "VAD sustained speech, a non-filler STT partial/final, or typed input → generation bump → TTS cancel → transport clear."],
  ["Turn detection", "Implemented", "VAD separated from semantic end-of-turn (short answers, thinking pauses, noise, hard ceiling)."],
  ["Latency instrumentation", "Implemented", "Per-stage marks per turn, p50/p95 by provider mode; mock timings labelled as such."],
  ["Browser voice / typed", "Implemented", "Mic via AudioWorklet (16 kHz PCM16) when STT is configured; typed fallback otherwise."],
  ["Cloudflare / Cartesia / Twilio", "Adapters implemented", "Contract-tested against local fakes; live use requires credentials (not exercised in CI)."],
  ["Human transfer", "Domain-level", "Deterministic state; live PSTN <Dial> only when Twilio + transfer number are configured."],
  ["SMS / e-mail", "Interface + mock", "Notifier interface; promise confirmations go to a mock outbox."],
  [
    "NLU post-training (SFT)",
    "Done on synthetic data",
    "Gemma-2B + LoRA on 1,136 rows; frozen held-out exact match 0% → 90.6% (70B zero-shot 88.0%). Servable via Cloudflare BYO-LoRA (4.1 s p50, so 70B stays live); no customer data. See docs/POST_TRAINING_RESULTS.md.",
  ],
];

// Three tiers, derived from each row's status text (the status text itself is shown unchanged).
type Tier = "built" | "partial" | "sim";
const TIER: Record<string, Tier> = {
  Implemented: "built",
  "Adapters implemented": "partial",
  "Domain-level": "partial",
  "Done on synthetic data": "partial",
  "Interface + mock": "sim",
  Planned: "sim",
};
const TIERS: { key: Tier; title: string; note: string; tone: "ok" | "warn" | "mock" }[] = [
  { key: "built", title: "Implemented", note: "runs in every session and in CI", tone: "ok" },
  { key: "partial", title: "Partial", note: "built, but live use needs credentials or config", tone: "warn" },
  { key: "sim", title: "Simulated / planned", note: "interface with a mock behind it", tone: "mock" },
];

const LIFECYCLE = ["IDLE", "PROCESSING", "AGENT_SPEAKING", "LISTENING", "USER_SPEAKING", "PROCESSING", "AGENT_SPEAKING", "INTERRUPTED", "USER_SPEAKING", "…"];
const TERMINAL = ["TRANSFER_REQUESTED", "ENDED", "ERROR"];
const TONE: Record<string, string> = {
  IDLE: "listen",
  LISTENING: "listen",
  USER_SPEAKING: "caller",
  PROCESSING: "think",
  AGENT_SPEAKING: "agent",
  INTERRUPTED: "barge",
  TRANSFER_REQUESTED: "think",
  ENDED: "ended",
  ERROR: "barge",
};

const reveal = {
  initial: { opacity: 0, y: 24, filter: "blur(6px)" },
  whileInView: { opacity: 1, y: 0, filter: "blur(0px)" },
  viewport: { once: true, margin: "-60px" },
} as const;

export function Architecture() {
  const [focus, setFocus] = useState<"all" | "llm" | "auth">("all");
  const counts = TIERS.map((t) => ROWS.filter((r) => (TIER[r[1]] ?? "partial") === t.key).length);
  return (
    <MotionRoot>
      <main className={a.page}>
        <m.header className={a.head} initial={{ opacity: 0, y: 20, filter: "blur(8px)" }} animate={{ opacity: 1, y: 0, filter: "blur(0px)" }} transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1] }}>
          <span className={ui.overline}>/architecture</span>
          <h1 className={ui.h1}>
            One runtime.
            <br />
            <span className={ui.grad}>Two kinds of power.</span>
          </h1>
          <p className={ui.lead}>
            One runtime serves the browser demo, phone calls and the evaluation suite. Violet marks components that use a language model; green marks components that hold
            authority.
          </p>
        </m.header>

        <m.section className={a.diagramCard} {...reveal} transition={SPRING} aria-labelledby="pipe">
          <div className={a.diagramHead}>
            <h2 id="pipe" className={a.cardTitle}>
              Audio-to-audio pipeline
            </h2>
            <div className={ui.segmented} role="group" aria-label="Highlight components">
              {(
                [
                  ["all", "All"],
                  ["llm", "Language model"],
                  ["auth", "Authority"],
                ] as const
              ).map(([k, label]) => (
                <button key={k} type="button" aria-pressed={focus === k} onClick={() => setFocus(k)}>
                  {label}
                </button>
              ))}
            </div>
          </div>
          <div className={a.scroller} tabIndex={0} aria-label="Pipeline diagram (scrolls sideways on small screens)">
            <Pipeline focus={focus} />
          </div>
          <div className={a.legend}>
            <span data-k="llm">
              <i /> uses a language model
            </span>
            <span data-k="auth">
              <i /> holds authority
            </span>
            <span data-k="audio">
              <i /> audio / text flow
            </span>
            <span data-k="control">
              <i /> barge-in cancel
            </span>
            <span className={a.swipe}>← swipe →</span>
          </div>
        </m.section>

        <div className={a.split}>
          <m.section className={a.side} data-kind="llm" {...reveal} transition={SPRING} data-dim={focus === "auth"}>
            <span className={a.sideTag}>Language model</span>
            <h2 className={a.sideTitle}>Language models handle language</h2>
            <p>
              The LLM (Cloudflare Workers AI, JSON mode) turns a caller utterance into typed <code>ProposedAction</code>s such as:
            </p>
            <pre className={a.code}>
              <code>{`{"action":"PROPOSE_PAYMENT","amount":30000,"days_from_now":14}`}</code>
            </pre>
            <p>
              It never sees the date of birth or account terms. Invalid output fails closed to the deterministic parser; stop-contact and human requests are also
              detected by rules so a misclassification cannot drop them.
            </p>
          </m.section>
          <m.section className={a.side} data-kind="auth" {...reveal} transition={{ ...SPRING, delay: 0.08 }} data-dim={focus === "llm"}>
            <span className={a.sideTag}>Authority</span>
            <h2 className={a.sideTitle}>Application code owns authority</h2>
            <p>
              <code>ConversationController.apply()</code> validates every proposal through the policy engine and is the only writer of identity, disclosure, proposal,
              promise, stop-contact and transfer state. Replies come from a plan of approved facts; an output guard rejects amounts, dates or threats the plan did not
              approve.
            </p>
          </m.section>
        </div>

        <m.section className={a.card} {...reveal} transition={SPRING} aria-labelledby="life">
          <h2 id="life" className={a.cardTitle}>
            Voice lifecycle
          </h2>
          <ol className={a.chain} aria-label="Voice lifecycle states in order">
            {LIFECYCLE.map((s, i) => (
              <li key={i} data-tone={TONE[s] ?? "plain"} style={{ animationDelay: `${i * 0.6}s` }}>
                {s}
              </li>
            ))}
            <li className={a.terminal}>
              {TERMINAL.map((s) => (
                <span key={s} data-tone={TONE[s]}>
                  {s}
                </span>
              ))}
            </li>
          </ol>
          <p className={a.note}>
            Transitions are validated against an explicit table and recorded with cause and timestamp. Barge-in timestamps: <code>barge_in_detected_at</code>,{" "}
            <code>tts_cancel_requested_at</code>, <code>tts_stopped_at</code>.
          </p>
        </m.section>

        <m.section className={a.card} {...reveal} transition={SPRING} aria-labelledby="status">
          <h2 id="status" className={a.cardTitle}>
            Implemented vs simulated vs planned
          </h2>
          <div className={a.tiers}>
            {TIERS.map((t, i) => (
              <div key={t.key} className={a.tier} data-tier={t.key}>
                <b>{counts[i]}</b>
                <span>
                  <Chip tone={t.tone} dot={t.tone !== "mock"}>
                    {t.title}
                  </Chip>
                  <em>{t.note}</em>
                </span>
              </div>
            ))}
          </div>
          <table className={a.table}>
            <thead>
              <tr>
                <th scope="col">Component</th>
                <th scope="col">Status</th>
                <th scope="col">Notes</th>
              </tr>
            </thead>
            {TIERS.map((t) => {
              const rows = ROWS.filter((r) => (TIER[r[1]] ?? "partial") === t.key);
              if (!rows.length) return null;
              return (
                <tbody key={t.key} data-tier={t.key}>
                  <tr className={a.groupRow}>
                    <th colSpan={3} scope="colgroup">
                      {t.title} · {rows.length}
                    </th>
                  </tr>
                  {rows.map(([name, status, notes]) => (
                    <tr key={name}>
                      <th scope="row" className={a.compName}>
                        {name}
                      </th>
                      <td className={a.statusCell}>
                        <Chip tone={t.tone}>{status}</Chip>
                      </td>
                      <td className={a.notes}>{notes}</td>
                    </tr>
                  ))}
                </tbody>
              );
            })}
          </table>
        </m.section>
      </main>
    </MotionRoot>
  );
}
