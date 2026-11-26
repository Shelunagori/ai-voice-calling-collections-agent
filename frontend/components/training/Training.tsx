"use client";

// /model-training — static summary of the NLU post-training work. Every number and example on this page is copied
// from docs/POST_TRAINING_RESULTS.md, docs/DECISIONS.md (D19) and backend/training/reports/ (held-out n=117).
// No data fetching.

import {
  animate,
  m,
  useInView,
  useMotionValue,
  useTransform,
} from "framer-motion";
import { useEffect, useRef } from "react";
import { Chip } from "@/components/ui/Chip";
import { cx } from "@/components/ui/cx";
import { usePrefersReducedMotion } from "@/components/ui/hooks";
import { MotionRoot, SPRING } from "@/components/ui/motion";
import ui from "@/components/ui/ui.module.css";
import t from "./training.module.css";

const HF_URL = "https://huggingface.co/shailendranagori7/vca-nlu-gemma-2b-lora";

const reveal = {
  initial: { opacity: 0, y: 24, filter: "blur(6px)" },
  whileInView: { opacity: 1, y: 0, filter: "blur(0px)" },
  viewport: { once: true, margin: "-60px" },
} as const;

/** Counts up from 0 when scrolled into view; shows the final value immediately with reduced motion (and on the server). */
function Num({ value, decimals = 0 }: { value: number; decimals?: number }) {
  const reduced = usePrefersReducedMotion();
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true, margin: "-40px" });
  const mv = useMotionValue(value);
  const text = useTransform(mv, (v) =>
    v.toLocaleString("en-US", {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    }),
  );
  useEffect(() => {
    if (!inView || reduced) return;
    mv.set(0);
    const c = animate(mv, value, { duration: 1.4, ease: [0.16, 1, 0.3, 1] });
    return () => c.stop();
  }, [inView, reduced, mv, value]);
  return <m.span ref={ref}>{text}</m.span>;
}

// Held-out row multi_intent-0837 (NEGOTIATION phase). Outputs are the recorded raw completions:
// before = reports/before-gemma2b-zeroshot.json, after = reports/after-gemma2b-lora-local.json.
const EXAMPLE = {
  agent: "How much could you pay, and when?",
  caller: "Fine, 30,000 on October 20, but don't call me again after that.",
  before: `{
  "action": "PROPOSE_PAYMENT",
  "amount": 30000,
  "date": "2026-10-20"
}`,
  after: `{"actions": [
  {"action": "STOP_CONTACT"},
  {"action": "PROPOSE_PAYMENT",
   "amount": 30000, "date": "2026-10-20"}
]}`,
};

const HOW: {
  n: number;
  decimals?: number;
  unit?: string;
  label: string;
  note: string;
}[] = [
  {
    n: 1136,
    label: "synthetic training rows",
    note: "gold labels known by construction; no customer data",
  },
  { n: 117, label: "frozen test rows", note: "held out, md5 pinned by a test" },
  {
    n: 8,
    unit: "r",
    label: "LoRA rank",
    note: "α=16, attention projections only, 2 epochs",
  },
  {
    n: 7.4,
    decimals: 1,
    unit: "MB",
    label: "adapter",
    note: "trained on a free Colab T4 in 114 min",
  },
];

type Row = {
  name: string;
  note: string;
  exact: number;
  recall: number;
  p50: string;
  kind?: "base" | "best" | "live" | "zero";
};
const RESULTS: Row[] = [
  {
    name: "Gemma-2B, untrained",
    note: "zero-shot; did not know the schema",
    exact: 0,
    recall: 0,
    p50: "—",
    kind: "zero",
  },
  {
    name: "Rules parser",
    note: "deterministic, hand-written",
    exact: 88.9,
    recall: 100,
    p50: "~0 ms",
  },
  {
    name: "Llama-3.3-70B",
    note: "Cloudflare, zero-shot JSON",
    exact: 88.0,
    recall: 100,
    p50: "1,005 ms",
    kind: "base",
  },
  {
    name: "Gemma-2B + LoRA v1",
    note: "737 rows, local T4",
    exact: 82.9,
    recall: 94.1,
    p50: "—",
  },
  {
    name: "Gemma-2B + LoRA v2",
    note: "1,136 rows, local T4",
    exact: 90.6,
    recall: 94.1,
    p50: "1.3 s batched",
    kind: "best",
  },
  {
    name: "LoRA v2 on Cloudflare",
    note: "BYO-LoRA beta serving",
    exact: 85.5,
    recall: 94.1,
    p50: "4.1 s",
  },
  {
    name: "Live system (70B + rules merge)",
    note: "what the agent runs today",
    exact: 92.3,
    recall: 100,
    p50: "940 ms",
    kind: "live",
  },
];

export function Training() {
  return (
    <MotionRoot>
      <main className={t.page}>
        {/* 1. hero */}
        <m.header
          className={t.head}
          initial={{ opacity: 0, y: 20, filter: "blur(8px)" }}
          animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
          transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1] }}
        >
          <span className={ui.overline}>/model-training</span>
          <h1 className={ui.h1}>
            Smaller model.
            <br />
            <span className={ui.grad}>Better answers.</span>
          </h1>
          <p className={ui.lead}>
            Gemma-2B fine-tuned with LoRA for the language-understanding step
            only: turning what the caller said into a typed action. It is not
            live by default.
          </p>
          <div className={t.chips}>
            <Chip tone="info" dot>
              NLU step only
            </Chip>
            <Chip tone="ok" dot>
              90.6% vs 88.0% for 70B
            </Chip>
            <Chip tone="mock">not live by default</Chip>
          </div>
        </m.header>

        {/* 2. what it learned */}
        <m.section
          className={t.card}
          {...reveal}
          transition={SPRING}
          aria-labelledby="learned"
        >
          <h2 id="learned" className={t.cardTitle}>
            What it learned
          </h2>
          <p className={t.text}>
            One caller sentence in, one JSON action list out. A real held-out
            test row:
          </p>
          <div className={t.convo}>
            <div className={t.bubble} data-who="agent">
              <span className={t.who}>Agent</span>
              {EXAMPLE.agent}
            </div>
            <div className={t.bubble} data-who="caller">
              <span className={t.who}>Caller</span>
              {EXAMPLE.caller}
            </div>
          </div>
          <div className={t.compare}>
            <div className={t.out} data-tone="bad">
              <div className={t.outHead}>
                <Chip tone="bad" dot>
                  untrained Gemma-2B
                </Chip>
                <span>
                  wrong format · &ldquo;don&apos;t call me&rdquo; dropped
                </span>
              </div>
              <pre className={t.code}>{EXAMPLE.before}</pre>
            </div>
            <div className={t.arrow} aria-hidden="true">
              →
            </div>
            <div className={t.out} data-tone="ok">
              <div className={t.outHead}>
                <Chip tone="ok" dot>
                  after LoRA v2
                </Chip>
                <span>exact match</span>
              </div>
              <pre className={t.code}>{EXAMPLE.after}</pre>
            </div>
          </div>
          <p className={t.authority}>
            <Chip tone="ok">deterministic</Chip>
            <span>
              The model only proposes actions. Identity checks, payment
              approval, stop-contact and promises stay in deterministic code;
              training never touched them.
            </span>
          </p>
        </m.section>

        {/* 3. how */}
        <m.section
          className={t.card}
          {...reveal}
          transition={SPRING}
          aria-labelledby="how"
        >
          <h2 id="how" className={t.cardTitle}>
            How
          </h2>
          <div className={t.stats}>
            {HOW.map((h, i) => (
              <m.div
                key={h.label}
                className={t.stat}
                initial={{ opacity: 0, y: 14 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true, margin: "-40px" }}
                transition={{ ...SPRING, delay: i * 0.06 }}
              >
                <b>
                  {h.unit === "r" && <small>r=</small>}
                  <Num value={h.n} decimals={h.decimals} />
                  {h.unit === "MB" && <small> MB</small>}
                </b>
                <span>{h.label}</span>
                <em>{h.note}</em>
              </m.div>
            ))}
          </div>
          <p className={t.text}>
            Trained, evaluated and served with{" "}
            <b>the same prompt as production</b> (the production system prompt
            verbatim, checked by a test), so the benchmark measures what the
            agent would actually see. Adapter:{" "}
            <a
              href={HF_URL}
              target="_blank"
              rel="noreferrer"
              className={t.link}
            >
              huggingface.co/shailendranagori7/vca-nlu-gemma-2b-lora ↗
            </a>
          </p>
        </m.section>

        {/* 4. results */}
        <m.section
          className={t.card}
          {...reveal}
          transition={SPRING}
          aria-labelledby="results"
        >
          <div className={t.resultsHead}>
            <h2 id="results" className={t.cardTitle}>
              Results
            </h2>
            <span className={t.caption}>
              exact match on 117 frozen held-out rows
            </span>
          </div>
          <table className={t.table}>
            <thead>
              <tr>
                <th scope="col">Model</th>
                <th scope="col">Exact match</th>
                <th scope="col">Rights recall</th>
                <th scope="col">Latency p50</th>
              </tr>
            </thead>
            <tbody>
              {RESULTS.map((r, i) => (
                <tr key={r.name} data-kind={r.kind ?? "plain"}>
                  <th scope="row">
                    <b>{r.name}</b>
                    <small>{r.note}</small>
                  </th>
                  <td className={t.exact}>
                    <div className={t.exactIn}>
                      <span className={t.track} aria-hidden="true">
                        <m.i
                          style={{ width: `${r.exact}%` }}
                          initial={{ scaleX: 0 }}
                          whileInView={{ scaleX: 1 }}
                          viewport={{ once: true, margin: "-30px" }}
                          transition={{
                            duration: 1.1,
                            delay: 0.05 * i,
                            ease: [0.16, 1, 0.3, 1],
                          }}
                        />
                      </span>
                      <span className={t.pct}>
                        <Num value={r.exact} decimals={1} />%
                      </span>
                    </div>
                  </td>
                  <td data-label="Rights recall">
                    <span
                      className={cx(t.recall, r.recall < 100 && t.recallMiss)}
                    >
                      {r.recall === 0 ? "0%" : `${r.recall}%`}
                    </span>
                  </td>
                  <td data-label="p50" className={t.mono}>
                    {r.p50}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className={t.caption}>
            Rights recall: a stop-contact or &ldquo;let me talk to a
            person&rdquo; request is never dropped (a hard gate).
            &ldquo;—&rdquo; means latency was not measured on that run.
          </p>
        </m.section>

        {/* 5. why not shipped */}
        <m.section
          className={t.card}
          {...reveal}
          transition={SPRING}
          aria-labelledby="why"
        >
          <h2 id="why" className={t.cardTitle}>
            Why it&apos;s not shipped
          </h2>
          <div className={t.whyGrid}>
            <div className={t.why}>
              <span className={ui.overline}>
                1 · too slow on this serving path
              </span>
              <div className={t.race}>
                <div>
                  <span>LoRA v2 on Cloudflare</span>
                  <span className={t.raceBar} data-tone="bad">
                    <m.i
                      initial={{ scaleX: 0 }}
                      whileInView={{ scaleX: 1 }}
                      viewport={{ once: true }}
                      transition={{ duration: 1.2, ease: [0.16, 1, 0.3, 1] }}
                      style={{ width: "100%" }}
                    />
                  </span>
                  <b>
                    <Num value={4.1} decimals={1} /> s
                  </b>
                </div>
                <div>
                  <span>Llama-70B (live)</span>
                  <span className={t.raceBar} data-tone="ok">
                    <m.i
                      initial={{ scaleX: 0 }}
                      whileInView={{ scaleX: 1 }}
                      viewport={{ once: true }}
                      transition={{ duration: 1.2, ease: [0.16, 1, 0.3, 1] }}
                      style={{ width: "24%" }}
                    />
                  </span>
                  <b>~1 s</b>
                </div>
              </div>
              <p className={t.text}>
                Cloudflare&apos;s LoRA beta answered in 4.1 s at p50, outside
                the 1.5 s turn budget. The 70B model answers in about one
                second.
              </p>
            </div>
            <div className={t.why}>
              <span className={ui.overline}>
                2 · one rights miss on its own
              </span>
              <p className={t.quote}>
                <span lang="ja">「責任者を出してください」</span>
                <small>
                  &ldquo;Please let me talk to a manager&rdquo; → read as
                  UNCLEAR
                </small>
              </p>
              <p className={t.text}>
                Alone, the model missed one Japanese request for a human: rights
                recall <b>94.1%</b>. The deterministic rules safety net catches
                it, which brings the live path to 100%.
              </p>
            </div>
          </div>
          <p className={t.verdict}>
            <Chip tone="ok" dot>
              decision
            </Chip>
            <span>
              Production stays on Llama-70B + the rules safety net. The adapter
              is wired in behind a flag and stays off by default.
            </span>
          </p>
        </m.section>

        {/* 6. honest limits */}
        <m.section
          className={t.limits}
          {...reveal}
          transition={SPRING}
          aria-label="Limits"
        >
          <span className={ui.overline}>honest limits</span>
          <div className={t.chips}>
            <Chip tone="warn" dot>
              synthetic data
            </Chip>
            <Chip tone="warn" dot>
              Japanese rows not native-reviewed
            </Chip>
            <Chip tone="warn" dot>
              self-hosted latency not yet measured
            </Chip>
          </div>
        </m.section>
      </main>
    </MotionRoot>
  );
}
