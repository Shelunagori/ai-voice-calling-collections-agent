export const metadata = { title: "Architecture — AI Voice Collections Agent" };

function Box({ x, y, w = 150, h = 50, t, s, cls = "" }: { x: number; y: number; w?: number; h?: number; t: string; s?: string; cls?: string }) {
  return (
    <g>
      <rect className={`box ${cls}`} x={x} y={y} width={w} height={h} rx={7} strokeWidth={1.5} />
      <text className="lbl" x={x + w / 2} y={y + (s ? 21 : 29)} textAnchor="middle">
        {t}
      </text>
      {s && (
        <text className="sub" x={x + w / 2} y={y + 37} textAnchor="middle">
          {s}
        </text>
      )}
    </g>
  );
}

function Pipeline() {
  return (
    <svg className="diagram" viewBox="0 0 940 390" role="img" aria-label="Audio-to-audio pipeline">
      <defs>
        <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0,0 L10,5 L0,10 z" fill="currentColor" style={{ color: "var(--muted)" }} />
        </marker>
      </defs>
      {/* row 1: audio in */}
      <Box x={10} y={20} t="Caller" s="browser mic / PSTN" />
      <Box x={200} y={20} t="Transport" s="WebSocket · Twilio Media" />
      <Box x={390} y={20} t="VAD" s="energy + noise floor" />
      <Box x={580} y={20} t="Streaming STT" s="Cartesia Ink / mock" />
      <Box x={770} y={20} w={160} t="End-of-turn" s="semantic silence rules" />
      {/* row 2 */}
      <Box x={390} y={120} t="Barge-in" s="gen bump · cancel · clear" />
      <Box x={770} y={120} w={160} t="Understanding" s="LLM JSON + rules net" cls="llm" />
      {/* row 3: audio out (right to left) */}
      <Box x={200} y={220} t="Paced playback" s="generation-tagged" />
      <Box x={390} y={220} t="Streaming TTS" s="Cartesia Sonic / mock" />
      <Box x={580} y={220} t="Realizer + guard" s="templates / LLM rephrase" cls="llm" />
      <Box x={770} y={220} w={160} t="Controller.apply()" s="authoritative state" cls="auth" />
      {/* row 4 */}
      <Box x={580} y={320} t="Audit + DB" s="PostgreSQL" />
      <Box x={770} y={320} w={160} t="Policy engine" s="deterministic, audited" cls="auth" />

      <path className="edge" d="M160 45 H200" markerStart="url(#arrow)" />
      <path className="edge" d="M350 45 H390" />
      <path className="edge" d="M540 45 H580" />
      <path className="edge" d="M730 45 H770" />
      <path className="edge" d="M850 70 V120" />
      <path className="edge" d="M850 170 V220" />
      <path className="edge" d="M770 245 H730" />
      <path className="edge" d="M580 245 H540" />
      <path className="edge" d="M390 245 H350" />
      <path className="edge" d="M275 220 V70" />
      <path className="edge" d="M850 270 V320" markerStart="url(#arrow)" />
      <path className="edge" d="M780 270 L700 320" strokeDasharray="4 3" />
      <path className="edge" d="M465 70 V120" />
      <path className="edge" d="M430 170 L330 220" strokeDasharray="4 3" />
      <path className="edge" d="M480 170 V220" strokeDasharray="4 3" />
      <text className="sub" x={284} y={150}>
        audio out
      </text>
      <text className="sub" x={488} y={200}>
        cancel
      </text>
      <text className="sub" x={640} y={300}>
        every decision
      </text>
      <text className="sub" x={858} y={100}>
        turn text
      </text>
      <text className="sub" x={858} y={200}>
        typed proposal
      </text>
      <text className="sub" x={858} y={300}>
        check
      </text>
    </svg>
  );
}

const ROWS: [string, string, string][] = [
  ["Conversation controller", "Implemented", "Owns CollectionState; applies typed proposals only after policy checks."],
  ["Policy engine", "Implemented", "Identity-before-disclosure, envelope (min / max days / ≤ balance), no discounts, stop-contact, transfer, calling hours, attempt limits."],
  ["Promise-to-pay", "Implemented", "Requires verified identity, valid terms, a fully played read-back and an explicit yes; one per session (DB unique)."],
  ["Barge-in", "Implemented", "VAD sustained speech or non-filler STT partial → generation bump → TTS cancel → transport clear."],
  ["Turn detection", "Implemented", "VAD separated from semantic end-of-turn (short answers, thinking pauses, noise, hard ceiling)."],
  ["Latency instrumentation", "Implemented", "Per-stage marks per turn, p50/p95 by provider mode; mock timings labelled as such."],
  ["Browser voice / typed", "Implemented", "Mic via AudioWorklet (16 kHz PCM16) when STT is configured; typed fallback otherwise."],
  ["Cloudflare / Cartesia / Twilio", "Adapters implemented", "Contract-tested against local fakes; live use requires credentials (not exercised in CI)."],
  ["Human transfer", "Domain-level", "Deterministic state; live PSTN <Dial> only when Twilio + transfer number are configured."],
  ["SMS / e-mail", "Interface + mock", "Notifier interface; promise confirmations go to a mock outbox."],
  ["Production post-training", "Planned", "See docs/POST_TRAINING_PLAN.md — nothing has been trained."],
];

export default function Architecture() {
  return (
    <main className="wrap narrow stack">
      <h1>Architecture</h1>
      <p className="muted">
        One runtime serves the browser demo, phone calls and the evaluation suite. Blue outlines mark components that use a language model; green outlines mark components
        that hold authority.
      </p>
      <div className="panel">
        <Pipeline />
      </div>
      <div className="grid cols-2">
        <div className="panel">
          <h2>Language models handle language</h2>
          <p className="small">
            The LLM (Cloudflare Workers AI, JSON mode) turns a caller utterance into typed <code>ProposedAction</code>s such as{" "}
            <code>{`{"action":"PROPOSE_PAYMENT","amount":30000,"days_from_now":14}`}</code>. It never sees the date of birth or account terms. Invalid output fails
            closed to the deterministic parser; stop-contact and human requests are also detected by rules so a misclassification cannot drop them.
          </p>
        </div>
        <div className="panel">
          <h2>Application code owns authority</h2>
          <p className="small">
            <code>ConversationController.apply()</code> validates every proposal through the policy engine and is the only writer of identity, disclosure,
            proposal, promise, stop-contact and transfer state. Replies come from a plan of approved facts; an output guard rejects amounts, dates or threats the plan
            did not approve.
          </p>
        </div>
      </div>
      <div className="panel">
        <h2>Voice lifecycle</h2>
        <p className="mono small">
          IDLE → PROCESSING → AGENT_SPEAKING → LISTENING → USER_SPEAKING → PROCESSING → AGENT_SPEAKING → INTERRUPTED → USER_SPEAKING → … → TRANSFER_REQUESTED | ENDED | ERROR
        </p>
        <p className="small muted">
          Transitions are validated against an explicit table and recorded with cause and timestamp. Barge-in timestamps: <code>barge_in_detected_at</code>,{" "}
          <code>tts_cancel_requested_at</code>, <code>tts_stopped_at</code>.
        </p>
      </div>
      <div className="panel table-scroll">
        <h2>Implemented vs simulated vs planned</h2>
        <table>
          <thead>
            <tr>
              <th>Component</th>
              <th>Status</th>
              <th>Notes</th>
            </tr>
          </thead>
          <tbody>
            {ROWS.map(([a, b, c]) => (
              <tr key={a}>
                <td>{a}</td>
                <td>
                  <span className={`pill ${b === "Implemented" ? "ok" : b === "Planned" ? "muted" : "warn"}`}>{b}</span>
                </td>
                <td className="small">{c}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </main>
  );
}
