"use client";

import Link from "next/link";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { AgentStatePanel, CollectionPanel, LatencyPanel, PolicyPanel, TranscriptPanel } from "@/components/console/Panels";
import { useCapabilities } from "@/components/StatusStrip";
import { MicCapture, PcmPlayer, parseAudioFrame } from "@/lib/audio";
import { getJSON, Scenario, wsUrl } from "@/lib/api";
import { yen } from "@/lib/format";
import { initialState, reduce } from "@/lib/session";

type Lang = "en" | "ja";

export default function DemoPage() {
  const { cap, error: capError } = useCapabilities();
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [scenarioKey, setScenarioKey] = useState("A");
  const [lang, setLang] = useState<Lang>("en");
  const [modeChoice, setMode] = useState<"auto" | "text" | "voice">("auto");
  const [speakMock, setSpeakMock] = useState(true);
  const [draft, setDraft] = useState("");
  const [state, dispatch] = useReducer(reduce, initialState);
  const ws = useRef<WebSocket | null>(null);
  const player = useRef<PcmPlayer | null>(null);
  const mic = useRef<MicCapture | null>(null);
  const [micError, setMicError] = useState<string | null>(null);

  useEffect(() => {
    getJSON<Scenario[]>("/api/scenarios").then(setScenarios).catch(() => setScenarios([]));
  }, []);
  const mode: "text" | "voice" = modeChoice === "auto" ? (cap?.browser_voice_available ? "voice" : "text") : modeChoice;

  const scenario = scenarios.find((s) => s.key === scenarioKey);
  const live = state.status === "live" || state.status === "connecting";
  const mockTts = !cap?.real_tts;

  const teardown = useCallback(() => {
    mic.current?.stop();
    mic.current = null;
    player.current?.close();
    player.current = null;
    if (typeof window !== "undefined") window.speechSynthesis?.cancel();
  }, []);

  useEffect(() => () => {
    ws.current?.close();
    teardown();
  }, [teardown]);

  // Voice mock replies locally when the server TTS is the silent mock (clearly labelled).
  const lastSpoken = useRef<string>("");
  useEffect(() => {
    if (!mockTts || !speakMock || typeof window === "undefined" || !window.speechSynthesis) return;
    const last = [...state.transcript].reverse().find((t) => t.speaker === "agent");
    if (!last || last.key === lastSpoken.current || last.interrupted) return;
    lastSpoken.current = last.key;
    const u = new SpeechSynthesisUtterance(last.text);
    u.lang = lang === "ja" ? "ja-JP" : "en-US";
    window.speechSynthesis.speak(u);
  }, [state.transcript, mockTts, speakMock, lang]);

  async function start() {
    setMicError(null);
    dispatch({ kind: "connecting" });
    player.current = new PcmPlayer(16000);
    await player.current.resume().catch(() => undefined);
    const sock = new WebSocket(wsUrl(`/ws/session?scenario=${scenarioKey}&lang=${lang}&mode=${mode}`));
    sock.binaryType = "arraybuffer";
    ws.current = sock;
    sock.onmessage = (m) => {
      if (m.data instanceof ArrayBuffer) {
        const { generation, pcm } = parseAudioFrame(m.data);
        player.current?.play(generation, pcm);
        return;
      }
      const ev = JSON.parse(m.data as string);
      if (ev.type === "audio.clear") {
        player.current?.clear(ev.generation);
        window.speechSynthesis?.cancel();
        return;
      }
      dispatch({ kind: "event", event: ev });
      if (ev.type === "session.created" && ev.input_mode === "voice") {
        const m2 = new MicCapture();
        mic.current = m2;
        m2.start((pcm) => sock.readyState === WebSocket.OPEN && sock.send(pcm)).catch((e: Error) => setMicError(e.message));
      }
    };
    sock.onclose = (e) => {
      dispatch({ kind: "socket_closed", reason: e.reason || undefined });
      teardown();
    };
  }

  function send(text: string) {
    if (!text.trim() || ws.current?.readyState !== WebSocket.OPEN) return;
    const clean = text.replace(/^\[interrupt\]\s*/, "");
    ws.current.send(JSON.stringify({ type: "text", text: clean }));
    setDraft("");
  }

  function interrupt() {
    ws.current?.send(JSON.stringify({ type: "interrupt" }));
  }

  function end() {
    ws.current?.send(JSON.stringify({ type: "end" }));
  }

  const script = scenario ? (lang === "ja" ? scenario.script_ja : scenario.script_en) : [];

  return (
    <main className="wrap">
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 12 }}>
        <div>
          <h1 style={{ fontSize: 22 }}>Browser voice demo</h1>
          <p className="muted small" style={{ margin: 0 }}>
            Synthetic debtor scenarios · every panel is rendered from server events · nothing here is a real account
          </p>
        </div>
        {state.sessionId && state.status === "ended" && (
          <Link className="btn" href={`/sessions?id=${state.sessionId}`}>
            Inspect audit trail →
          </Link>
        )}
      </div>

      {capError && <div className="banner" style={{ marginBottom: 12 }}>Backend unreachable: {capError}</div>}

      {!live && (
        <section className="grid cols-2" style={{ marginBottom: 14 }}>
          <div className="panel stack">
            <h3>1 · Language</h3>
            <div className="row">
              {(["en", "ja"] as Lang[]).map((l) => (
                <button key={l} className={`btn ${lang === l ? "primary" : ""}`} onClick={() => setLang(l)}>
                  {l === "en" ? "English" : "日本語"}
                </button>
              ))}
            </div>
            <h3>2 · Input</h3>
            <div className="row">
              <button className={`btn ${mode === "voice" ? "primary" : ""}`} disabled={!cap?.browser_voice_available} onClick={() => setMode("voice")}>
                Microphone
              </button>
              <button className={`btn ${mode === "text" ? "primary" : ""}`} onClick={() => setMode("text")}>
                Typed input
              </button>
            </div>
            {!cap?.browser_voice_available && (
              <p className="small muted">
                Speech recognition credentials are not configured on this deployment, so the demo uses typed input with the same runtime (turn-taking, barge-in, policy,
                latency instrumentation). Set <code>STT_PROVIDER=cartesia</code> to enable the microphone.
              </p>
            )}
            {mockTts && (
              <label className="small row">
                <input type="checkbox" checked={speakMock} onChange={(e) => setSpeakMock(e.target.checked)} /> Read agent replies aloud with the browser&apos;s own speech
                synthesis (local stand-in; server TTS is the mock)
              </label>
            )}
            <button className="btn primary" onClick={start} disabled={!scenario}>
              Start voice session
            </button>
            <p className="small muted" style={{ margin: 0 }}>
              Use the synthetic details only — do not type or say real personal information. Browser demo transcripts are stored and listed publicly on this demo.
            </p>
          </div>
          <div className="panel">
            <h3>3 · Synthetic scenario</h3>
            <div className="grid" style={{ gap: 6 }}>
              {scenarios.map((s) => (
                <label key={s.key} className="row small" style={{ gap: 8, cursor: "pointer" }}>
                  <input type="radio" name="scenario" checked={scenarioKey === s.key} onChange={() => setScenarioKey(s.key)} />
                  <b className="mono">{s.key}</b> {lang === "ja" ? s.title_ja : s.title}
                  <span className="muted">— {s.summary}</span>
                </label>
              ))}
            </div>
            {scenario && (
              <dl className="kv" style={{ marginTop: 12 }}>
                <dt>Debtor (synthetic)</dt>
                <dd>{lang === "ja" ? scenario.debtor_name_ja : scenario.debtor_name}</dd>
                <dt>Date of birth</dt>
                <dd>{scenario.synthetic_date_of_birth} (use it to pass verification)</dd>
                <dt>Balance</dt>
                <dd>{yen(scenario.outstanding_balance, lang)}</dd>
                <dt>Envelope</dt>
                <dd>
                  min {yen(scenario.allowed_min_payment, lang)} · max {scenario.max_extension_days} days
                </dd>
              </dl>
            )}
          </div>
        </section>
      )}

      {(live || state.status === "ended") && (
        <>
          <section className="panel row" style={{ marginBottom: 14, justifyContent: "space-between" }}>
            <div className="row small">
              <span className={`pill ${state.status === "live" ? "ok" : state.status === "ended" ? "muted" : "warn"}`}>{state.status}</span>
              <span className="mono muted">{state.sessionId?.slice(0, 8)}</span>
              <span>
                Scenario <b>{scenarioKey}</b> · {lang.toUpperCase()} · input: {state.inputMode ?? mode}
              </span>
              {state.providers &&
                Object.entries(state.providers).map(([k, v]) => (
                  <span key={k} className={`pill ${v.startsWith("mock") || v === "fake" ? "muted" : "ok"}`}>
                    {k}:{v.split(":")[0]}
                  </span>
                ))}
              {state.endedReason && <span className="pill info">ended: {state.endedReason}</span>}
            </div>
            <div className="row">
              {state.status === "live" && (
                <>
                  <button className="btn sm" onClick={interrupt} disabled={state.voiceState !== "AGENT_SPEAKING"}>
                    Interrupt agent
                  </button>
                  <button className="btn sm danger" onClick={end}>
                    End session
                  </button>
                </>
              )}
              {state.status === "ended" && (
                <button className="btn sm" onClick={() => dispatch({ kind: "reset" })}>
                  New session
                </button>
              )}
            </div>
          </section>
          {micError && <div className="banner" style={{ marginBottom: 12 }}>Microphone unavailable ({micError}). Use typed input below.</div>}
          {state.errors.length > 0 && (
            <div className="banner" style={{ marginBottom: 12 }}>
              Degraded: {state.errors.slice(-2).join(" · ")}
            </div>
          )}
          <div className="console">
            <div className="stack">
              <TranscriptPanel items={state.transcript} partial={state.partial} lang={lang} />
              {state.status === "live" && (
                <div className="panel stack">
                  <form
                    className="row"
                    onSubmit={(e) => {
                      e.preventDefault();
                      send(draft);
                    }}
                  >
                    <input
                      type="text"
                      value={draft}
                      maxLength={500}
                      placeholder={state.inputMode === "voice" ? "Speak, or type to barge in…" : "Type what the caller says (typing while the agent speaks = barge-in)"}
                      onChange={(e) => setDraft(e.target.value)}
                      style={{ flex: 1, minWidth: 200 }}
                    />
                    <button className="btn primary" type="submit">
                      Send
                    </button>
                  </form>
                  <div className="chips">
                    {script.map((line) => (
                      <button key={line} className="chip" onClick={() => send(line)} title="Suggested caller line">
                        {line}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              <LatencyPanel items={state.latency} bargeIns={state.bargeIns} providers={state.providers} />
            </div>
            <div className="stack">
              <AgentStatePanel s={state} />
              <CollectionPanel c={state.collection} lang={lang} />
              <PolicyPanel items={state.policy} />
              {state.notifications.length > 0 && (
                <div className="panel small">
                  <h3>Channel notifications</h3>
                  {state.notifications.map((n, i) => (
                    <div key={i}>
                      {n.channel.toUpperCase()} via {n.provider}: {n.detail}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </main>
  );
}
