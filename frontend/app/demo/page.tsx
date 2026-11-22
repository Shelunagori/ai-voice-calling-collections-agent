"use client";

// Browser voice demo. Session logic is unchanged from the previous console (SessionManager + reducer); only the
// presentation is new. Everything on screen is derived from server events via lib/session.ts.
import { AnimatePresence, LayoutGroup, m } from "framer-motion";
import Link from "next/link";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { CallControlsHint, Composer } from "@/components/demo/Composer";
import { EndSummary, LiveTranscript, PolicyStream, PromiseCard, type RawBargeIn, Timeline, UnderTheHood, fmtClock } from "@/components/demo/CallParts";
import d from "@/components/demo/demo.module.css";
import { LevelMeter, meteredDeps, synthesizedLevel } from "@/components/demo/levels";
import { Setup, SetupHead } from "@/components/demo/Setup";
import { speak, stopSpeaking, warmVoices } from "@/components/demo/speech";
import { VoiceOrb } from "@/components/orb/VoiceOrb";
import type { OrbSignal } from "@/components/orb/types";
import { useCapabilities } from "@/components/StatusStrip";
import { Chip } from "@/components/ui/Chip";
import { cx } from "@/components/ui/cx";
import { usePrefersReducedMotion } from "@/components/ui/hooks";
import { MotionRoot, SPRING } from "@/components/ui/motion";
import { StateIndicator } from "@/components/ui/StateIndicator";
import ui from "@/components/ui/ui.module.css";
import { getJSON, Scenario, wsUrl } from "@/lib/api";
import { initialState, reduce, type ServerEvent } from "@/lib/session";
import { SessionManager } from "@/lib/voice-client";

type Lang = "en" | "ja";
// How long a barge-in stays visible. In typed mode the server passes through INTERRUPTED in about 1 ms, which no
// eye (or animation frame) would catch, so the real barge_in event is held on screen this long.
const BARGE_HOLD_MS = 650;
const isMock = (v: string) => v.startsWith("mock") || v === "fake";

export default function DemoPage() {
  const { cap, error: capError } = useCapabilities();
  const reduced = usePrefersReducedMotion();
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [scenarioKey, setScenarioKey] = useState("A");
  const [lang, setLang] = useState<Lang>("en");
  const [modeChoice, setMode] = useState<"auto" | "text" | "voice">("auto");
  const [speakMock, setSpeakMock] = useState(true);
  const [draft, setDraft] = useState("");
  const [state, dispatch] = useReducer(reduce, initialState);
  const manager = useRef<SessionManager | null>(null);
  const meter = useRef<LevelMeter | null>(null);
  const starting = useRef(false);
  const stageRef = useRef<HTMLElement>(null);
  const [notices, setNotices] = useState<string[]>([]);

  // client-clock bookkeeping, recorded in event handlers (never during render)
  const [now, setNow] = useState(0);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [endedAt, setEndedAt] = useState<number | null>(null);
  const [bargeAt, setBargeAt] = useState(-1e9);
  const [flashKey, setFlashKey] = useState(0);
  const [lcSync, setLcSync] = useState<{ server: number; client: number } | null>(null);

  useEffect(() => {
    getJSON<Scenario[]>("/api/scenarios").then(setScenarios).catch(() => setScenarios([]));
  }, []);
  const mode: "text" | "voice" = modeChoice === "auto" ? (cap?.browser_voice_available ? "voice" : "text") : modeChoice;
  const scenario = scenarios.find((s) => s.key === scenarioKey);
  const live = state.status === "live" || state.status === "connecting";
  const inCall = live || state.status === "ended";
  const mockTts = !cap?.real_tts;
  const inputMode = state.inputMode ?? mode;

  const getManager = useCallback(() => {
    if (!meter.current) meter.current = new LevelMeter();
    if (!manager.current) manager.current = new SessionManager(meteredDeps(meter.current));
    return manager.current;
  }, []);

  useEffect(() => {
    warmVoices();
    return () => {
      manager.current?.stop();
      stopSpeaking();
    };
  }, []);

  // Clock for the timer / timeline while a call is up.
  useEffect(() => {
    if (!inCall) return;
    const id = setInterval(() => setNow(performance.now()), 200);
    return () => clearInterval(id);
  }, [inCall]);

  // Voice mock replies locally when the server TTS is the silent mock (clearly labelled). The "already spoken"
  // key includes the session id: turn keys restart at a0 every call, so a turn-only key skipped the greeting of
  // every call after the first.
  const lastSpoken = useRef<string>("");
  useEffect(() => {
    if (!mockTts || !speakMock || state.status !== "live") return;
    const last = [...state.transcript].reverse().find((t) => t.speaker === "agent");
    const key = last ? `${state.sessionId}:${last.key}` : "";
    if (!last || key === lastSpoken.current || last.interrupted) return;
    lastSpoken.current = key;
    try {
      speak(last.text, lang);
    } catch (e) {
      console.error("[voice-demo] speech synthesis failed", e);
    }
  }, [state.transcript, state.status, state.sessionId, mockTts, speakMock, lang]);

  // ---- orb signal: read every animation frame from refs (no React render per frame) ----
  const live$ = useRef({ voiceState: "IDLE", inCall: false, voice: false, bargeAt: -1e9 });
  useEffect(() => {
    live$.current = { voiceState: state.voiceState, inCall, voice: inputMode === "voice", bargeAt };
  }, [state.voiceState, inCall, inputMode, bargeAt]);
  const signal = useRef<() => OrbSignal>(() => ({ state: "IDLE", amp: 0 }));
  useEffect(() => {
    signal.current = () => {
      const t = performance.now();
      const L = live$.current;
      if (!L.inCall) return { state: "IDLE", amp: 0 };
      const st = t - L.bargeAt < BARGE_HOLD_MS ? "INTERRUPTED" : L.voiceState;
      if (!L.voice) return { state: st, amp: synthesizedLevel(st, t) };
      const mtr = meter.current;
      const amp = !mtr ? 0 : st === "AGENT_SPEAKING" ? mtr.out(t) : Math.max(mtr.mic(t), mtr.out(t));
      return { state: st, amp };
    };
  }, []);

  const onEvent = useCallback((event: ServerEvent) => {
    const t = performance.now();
    if (event.type === "session.created") setStartedAt(t);
    if (event.type === "lifecycle" && typeof event.at === "number") {
      setLcSync({ server: event.at, client: t });
    }
    if (event.type === "barge_in") {
      setBargeAt(t);
      setFlashKey((k) => k + 1);
    }
    if (event.type === "session.ended") {
      setEndedAt(t);
      stopSpeaking(); // the call is over: the local voice must not keep talking
    }
    dispatch({ kind: "event", event });
  }, []);

  async function start() {
    // Guard against double clicks / retries: one connect at a time; the manager tears down any
    // previous session (socket, mic, player) and ignores its late callbacks.
    if (starting.current) return;
    starting.current = true;
    setNotices([]);
    setStartedAt(null);
    setEndedAt(null);
    setBargeAt(-1e9);
    setLcSync(null);
    dispatch({ kind: "connecting" });
    try {
      stageRef.current?.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" });
    } catch {
      /* cosmetic */
    }
    try {
      let url: string;
      try {
        url = wsUrl(`/ws/session?scenario=${encodeURIComponent(scenarioKey)}&lang=${lang}&mode=${mode}`);
      } catch (e) {
        console.error("[voice-demo] bad API base URL", e);
        setNotices(["The demo is misconfigured (invalid NEXT_PUBLIC_API_BASE_URL)."]);
        dispatch({ kind: "socket_closed", reason: "config_error" });
        return;
      }
      await getManager().start({
        url,
        onEvent,
        onNotice: (msg) => setNotices((n) => (n.includes(msg) ? n : [...n.slice(-3), msg])),
        onClosed: (reason) => {
          stopSpeaking();
          setEndedAt((e) => e ?? performance.now());
          dispatch({ kind: "socket_closed", reason });
        },
      });
    } catch (e) {
      // Defensive: the manager never throws, but a failure here must not reach React.
      console.error("[voice-demo] start failed", e);
      setNotices(["Could not start the session. Please retry."]);
      dispatch({ kind: "socket_closed", reason: "start_failed" });
    } finally {
      starting.current = false;
    }
  }

  function send(text: string) {
    const clean = text.replace(/^\[interrupt\]\s*/, "").trim();
    if (!clean) return;
    // The caller is talking: stop the local stand-in voice, as real barge-in clears agent audio.
    stopSpeaking();
    if (getManager().sendText(clean)) setDraft("");
  }
  const interrupt = () => {
    stopSpeaking();
    getManager().send({ type: "interrupt" });
    // After a manual barge-in the server waits for the caller: hand the turn to the reply box.
    document.getElementById("caller-input")?.focus();
  };
  const end = () => {
    stopSpeaking();
    if (!getManager().send({ type: "end" })) {
      // socket already gone: close locally so the button never appears to do nothing
      manager.current?.stop();
      dispatch({ kind: "socket_closed", reason: "caller_ended" });
    }
  };
  const reset = () => {
    stopSpeaking();
    lastSpoken.current = "";
    manager.current?.stop();
    dispatch({ kind: "reset" });
  };

  const script = scenario ? (lang === "ja" ? scenario.script_ja : scenario.script_en) : [];
  const elapsed = startedAt === null ? 0 : Math.max(0, (endedAt ?? now) - startedAt);
  const nowServer = lcSync && state.status === "live" ? lcSync.server + Math.max(0, now - lcSync.client) / 1000 : null;
  const shownState = state.status === "connecting" ? "PROCESSING" : now - bargeAt < BARGE_HOLD_MS ? "INTERRUPTED" : state.voiceState;
  // A manual interrupt leaves the server waiting for the caller (state stays INTERRUPTED): say so plainly.
  const yourTurn = state.status === "live" && state.voiceState === "INTERRUPTED" && now - bargeAt >= BARGE_HOLD_MS;
  const lastAgent = [...state.transcript].reverse().find((t) => t.speaker === "agent");
  const speakingKey = state.voiceState === "AGENT_SPEAKING" && lastAgent ? lastAgent.key : null;
  const c = state.collection;
  const debtor = scenario ? (lang === "ja" ? scenario.debtor_name_ja : scenario.debtor_name) : "";

  return (
    <MotionRoot>
      <main className={d.page}>
        <AnimatePresence>
          {flashKey > 0 && !reduced && (
            <m.div key={flashKey} className={d.flash} initial={{ opacity: 0 }} animate={{ opacity: [0, 0.9, 0] }} transition={{ duration: 0.65, times: [0, 0.15, 1] }} aria-hidden="true" />
          )}
        </AnimatePresence>
        <section ref={stageRef} className={cx(d.stage, inCall ? d.stageCall : d.stageHero)}>
          <LayoutGroup>
            <m.div layout className={d.orbCol} transition={SPRING}>
              <m.div layout className={d.orbBox} transition={SPRING}>
                <VoiceOrb signal={signal} tintPage />
              </m.div>
              <AnimatePresence mode="wait">
                {!inCall ? (
                  <m.div key="idle" className={d.idleState} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
                    <StateIndicator state={capError ? "ERROR" : "IDLE"} label={capError ? "Backend offline" : cap ? "Ready" : "Connecting to backend"} />
                    <SetupHead />
                  </m.div>
                ) : (
                  <m.div key="controls" className={d.controls} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ delay: 0.2 }}>
                    <StateIndicator state={yourTurn ? "LISTENING" : shownState} big label={state.status === "connecting" ? "Connecting" : yourTurn ? "Your turn" : undefined} />
                    <div className={d.timer}>
                      <span className={d.rec} data-live={state.status === "live"} /> {fmtClock(elapsed)}
                    </div>
                    <div className={d.ctrlRow}>
                      {state.status !== "ended" ? (
                        <>
                          <button className={ui.ghostBtn} onClick={interrupt} disabled={state.voiceState !== "AGENT_SPEAKING"}>
                            Interrupt agent
                          </button>
                          <button className={cx(ui.ghostBtn, ui.dangerBtn)} onClick={end} disabled={state.status !== "live"}>
                            End call
                          </button>
                        </>
                      ) : (
                        <button className={ui.ghostBtn} onClick={reset}>
                          New call
                        </button>
                      )}
                    </div>
                    <Timeline lifecycle={state.lifecycle} bargeIns={state.bargeIns as RawBargeIn[]} nowServer={nowServer} elapsedMs={elapsed} />
                    <CallControlsHint voice={inputMode === "voice"} />
                  </m.div>
                )}
              </AnimatePresence>
            </m.div>

            <AnimatePresence mode="popLayout">
              {!inCall ? (
                <Setup
                  key="setup"
                  cap={cap}
                  capError={capError}
                  scenarios={scenarios}
                  scenarioKey={scenarioKey}
                  onScenario={setScenarioKey}
                  lang={lang}
                  onLang={setLang}
                  mode={mode}
                  onMode={setMode}
                  mockTts={mockTts}
                  speakMock={speakMock}
                  onSpeakMock={setSpeakMock}
                  connecting={state.status === "connecting"}
                  onStart={() => void start()}
                />
              ) : (
                <m.div
                  key="call"
                  className={d.callPanel}
                  initial={{ opacity: 0, x: 40, filter: "blur(10px)" }}
                  animate={{ opacity: 1, x: 0, filter: "blur(0px)" }}
                  exit={{ opacity: 0, x: 30, filter: "blur(8px)", transition: { duration: 0.2 } }}
                  transition={{ ...SPRING, delay: 0.1 }}
                >
                  <div className={d.callHead}>
                    <div>
                      <div className={ui.overline}>
                        {state.status === "ended" ? "Call ended" : state.status === "connecting" ? "Connecting" : "Live call"} · scenario {scenarioKey} · {lang === "ja" ? "日本語" : "English"}
                      </div>
                      <div className={d.callTitle}>
                        {debtor} <span className={d.dim}>synthetic debtor</span>
                      </div>
                    </div>
                    <div className={d.headChips}>
                      {state.sessionId && <Chip title={state.sessionId}>{state.sessionId.slice(0, 8)}</Chip>}
                      <Chip tone={inputMode === "voice" ? "ok" : "plain"}>{inputMode === "voice" ? "mic" : "typed"}</Chip>
                      {state.providers &&
                        Object.entries(state.providers).map(([k, v]) => (
                          <Chip key={k} tone={isMock(v) ? "mock" : "ok"} title={v}>
                            {k} {isMock(v) ? "mock" : v.split(":")[0]}
                          </Chip>
                        ))}
                      {c?.stop_contact && (
                        <Chip tone="bad" dot>
                          stop-contact
                        </Chip>
                      )}
                      {c?.human_transfer_requested && (
                        <Chip tone="info" dot>
                          transfer {c.transfer_status.toLowerCase()}
                        </Chip>
                      )}
                    </div>
                  </div>

                  {notices.length > 0 && (
                    <div className={ui.banner} role="alert">
                      <div>
                        {notices.map((n) => (
                          <div key={n}>{n}</div>
                        ))}
                      </div>
                    </div>
                  )}
                  {state.errors.length > 0 && <div className={ui.banner}>Degraded: {state.errors.slice(-2).join(" · ")}</div>}

                  <LiveTranscript
                    items={state.transcript}
                    partial={state.partial}
                    speakingKey={speakingKey}
                    bargeIns={state.bargeIns as RawBargeIn[]}
                    empty={state.status === "connecting" ? "Connecting to the voice service…" : "Waiting for the agent…"}
                  />

                  {state.status === "live" && (
                    <Composer
                      draft={draft}
                      onDraft={setDraft}
                      onSend={send}
                      voice={state.inputMode === "voice"}
                      agentSpeaking={state.voiceState === "AGENT_SPEAKING"}
                      suggestions={script}
                    />
                  )}

                  {state.status === "ended" && (
                    <EndSummary
                      reason={state.endedReason}
                      c={c}
                      lang={lang}
                      elapsedMs={elapsed}
                      policy={state.policy}
                      bargeIns={state.bargeIns}
                      latency={state.latency}
                      turns={state.transcript.length}
                      actions={
                        <>
                          {state.sessionId && (
                            <Link className={ui.ghostBtn} href={`/sessions?id=${state.sessionId}`}>
                              Inspect audit trail →
                            </Link>
                          )}
                          <button className={ui.ghostBtn} onClick={reset}>
                            New call
                          </button>
                        </>
                      }
                    />
                  )}

                  <div className={d.callBottom}>
                    <PromiseCard c={c} lang={lang} />
                    <PolicyStream items={state.policy} />
                  </div>

                  <UnderTheHood
                    c={c}
                    lang={lang}
                    voiceState={state.voiceState}
                    lifecycle={state.lifecycle}
                    latency={state.latency}
                    providers={state.providers}
                    notifications={state.notifications}
                  />
                </m.div>
              )}
            </AnimatePresence>
          </LayoutGroup>
        </section>
      </main>
    </MotionRoot>
  );
}
