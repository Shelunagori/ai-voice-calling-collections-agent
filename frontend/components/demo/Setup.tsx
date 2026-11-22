"use client";

import { m } from "framer-motion";
import { BackendStatus } from "@/components/ui/BackendStatus";
import { Cta, MicIcon } from "@/components/ui/Cta";
import { cx } from "@/components/ui/cx";
import { SPRING } from "@/components/ui/motion";
import ui from "@/components/ui/ui.module.css";
import type { Capabilities, Scenario } from "@/lib/api";
import { yen } from "@/lib/format";
import d from "./demo.module.css";

type Lang = "en" | "ja";

/** "1988-04-12" -> "12-04-1988" (display only; anything unexpected is shown as-is). */
const dmy = (iso: string) => {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  return m ? `${m[3]}-${m[2]}-${m[1]}` : iso;
};

/** Headline + live status chips. Sits under the orb (sticky left column on desktop). */
export function SetupHead() {
  return (
      <div className={d.setupHead}>
        <span className={ui.overline}>/voice demo · live backend</span>
        <h1 className={ui.h1}>
          Talk to the agent.
          <br />
          <span className={ui.grad}>Watch policy decide.</span>
        </h1>
        <p className={ui.lead}>Synthetic debtor scenarios · every panel is rendered from server events · nothing here is a real account</p>
        <BackendStatus />
      </div>
  );
}

export function Setup({
  cap,
  capError,
  scenarios,
  scenarioKey,
  onScenario,
  lang,
  onLang,
  mode,
  onMode,
  mockTts,
  speakMock,
  onSpeakMock,
  noisyPlace,
  onNoisyPlace,
  connecting,
  onStart,
}: {
  cap: Capabilities | null;
  capError: string | null;
  scenarios: Scenario[];
  scenarioKey: string;
  onScenario: (k: string) => void;
  lang: Lang;
  onLang: (l: Lang) => void;
  mode: "text" | "voice";
  onMode: (m: "text" | "voice") => void;
  mockTts: boolean;
  speakMock: boolean;
  onSpeakMock: (v: boolean) => void;
  noisyPlace: boolean;
  onNoisyPlace: (v: boolean) => void;
  connecting: boolean;
  onStart: () => void;
}) {
  const scenario = scenarios.find((s) => s.key === scenarioKey);
  return (
    <m.div
      className={d.setup}
      initial={{ opacity: 0, y: 24, filter: "blur(8px)" }}
      animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
      exit={{ opacity: 0, y: -12, filter: "blur(10px)", transition: { duration: 0.25 } }}
      transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1] }}
    >
      {capError && <div className={cx(ui.banner, ui.bannerBad)}>Backend unreachable: {capError}</div>}

      <section className={d.setupCard} aria-label="Call setup">
        <div className={d.setupRow}>
          <div className={d.field}>
            <span className={ui.overline}>1 · Language</span>
            <div className={ui.segmented} role="group" aria-label="Language">
              {(["en", "ja"] as Lang[]).map((l) => (
                <button key={l} type="button" aria-pressed={lang === l} onClick={() => onLang(l)}>
                  {l === "en" ? "English" : "日本語"}
                </button>
              ))}
            </div>
          </div>
          <div className={d.field}>
            <span className={ui.overline}>2 · Input</span>
            <div className={ui.segmented} role="group" aria-label="Input mode">
              <button type="button" aria-pressed={mode === "voice"} disabled={!cap?.browser_voice_available} onClick={() => onMode("voice")}>
                Microphone
              </button>
              <button type="button" aria-pressed={mode === "text"} onClick={() => onMode("text")}>
                Typed input
              </button>
            </div>
          </div>
        </div>
        {mode === "voice" && cap?.browser_voice_available && <p className={ui.fine}>For best barge-in and speech recognition results, use headphones and a quiet environment.</p>}
        {cap && !cap.browser_voice_available && (
          <p className={ui.fine}>
            Speech recognition credentials are not configured on this deployment, so the demo uses typed input with the same runtime (turn-taking, barge-in, policy,
            latency instrumentation). Set <code>STT_PROVIDER=cartesia</code> to enable the microphone.
          </p>
        )}

        <div className={d.field}>
          <span className={ui.overline}>3 · Synthetic scenario</span>
          <div className={d.scenarios} role="radiogroup" aria-label="Synthetic scenario">
            {scenarios.length === 0 && !capError && <span className={d.skel} style={{ height: 76 }} />}
            {scenarios.map((s, i) => (
              <m.label
                key={s.key}
                className={d.scn}
                title={s.summary}
                data-on={s.key === scenarioKey}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ ...SPRING, delay: i * 0.04 }}
              >
                <input type="radio" name="scenario" value={s.key} checked={s.key === scenarioKey} onChange={() => onScenario(s.key)} />
                <b>{s.key}</b>
                <span className={d.scnText}>
                  <span className={d.scnTitle}>{lang === "ja" ? s.title_ja : s.title}</span>
                  <span className={d.scnSum}>{s.summary}</span>
                </span>
              </m.label>
            ))}
          </div>
        </div>

        {scenario && (
          <dl className={d.debtor}>
            <div>
              <dt>Debtor (synthetic)</dt>
              <dd>{lang === "ja" ? scenario.debtor_name_ja : scenario.debtor_name}</dd>
            </div>
            <div>
              <dt>Date of birth · use it to verify</dt>
              <dd className={ui.mono}>{dmy(scenario.synthetic_date_of_birth)}</dd>
            </div>
            <div>
              <dt>Balance</dt>
              <dd className={ui.mono}>{yen(scenario.outstanding_balance, lang)}</dd>
            </div>
            <div>
              <dt>Envelope</dt>
              <dd className={ui.mono}>
                min {yen(scenario.allowed_min_payment, lang)} · ≤ {scenario.max_extension_days} d
              </dd>
            </div>
          </dl>
        )}

        {mode === "voice" && cap?.browser_voice_available && (
          <label className={d.check2}>
            <input type="checkbox" checked={noisyPlace} onChange={(e) => onNoisyPlace(e.target.checked)} />
            <span>
              Noisy place: mute my microphone while the agent is talking, so traffic or people nearby can&apos;t interrupt it. Use <b>Interrupt agent</b> to cut in.
            </span>
          </label>
        )}
        {mockTts && (
          <label className={d.check2}>
            <input type="checkbox" checked={speakMock} onChange={(e) => onSpeakMock(e.target.checked)} />
            <span>Read agent replies aloud with the browser&apos;s own speech synthesis (local stand-in; server TTS is the mock)</span>
          </label>
        )}

        <div className={d.startRow}>
          <Cta onClick={onStart} disabled={!scenario || connecting}>
            <MicIcon />
            {connecting ? "Connecting…" : "Start a voice call"}
          </Cta>
          <p className={ui.fine}>
            Use the synthetic details only — do not type or say real personal information. Browser demo transcripts are stored and listed publicly on this demo.
          </p>
        </div>
      </section>
    </m.div>
  );
}
