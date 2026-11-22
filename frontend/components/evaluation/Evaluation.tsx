"use client";

// /evaluation — real stored runs from the backend (GET /api/eval/runs, /api/eval/runs/{id}); the run button calls
// POST /api/eval/run. Deterministic invariants decide pass/fail; judge scores are shown as supplementary only.
import { AnimatePresence, animate, m, useInView, useMotionValue, useMotionValueEvent, useTransform } from "framer-motion";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Chip } from "@/components/ui/Chip";
import { Cta } from "@/components/ui/Cta";
import { cx } from "@/components/ui/cx";
import { usePrefersReducedMotion } from "@/components/ui/hooks";
import { MotionRoot, SPRING } from "@/components/ui/motion";
import ui from "@/components/ui/ui.module.css";
import { getJSON } from "@/lib/api";
import { humanize } from "@/lib/format";
import e from "./eval.module.css";

type Run = {
  id: string;
  started_at: string;
  total: number;
  passed: number;
  llm_provider?: string;
  llm_model?: string;
  judge_provider: string;
  judge_model: string;
  judge_prompt_version: string;
  code_version: string;
  summary: { by_category: Record<string, { total: number; passed: number }>; clock: string; judge_note: string };
};
type Inv = { name: string; passed: boolean; detail: string };
type Result = {
  case_key: string;
  title: string;
  category: string;
  language: string;
  passed: boolean;
  invariants: Inv[];
  judge: { scores: Record<string, { score: number; reason: string }> | null; provider: string; model?: string } | null;
  transcript: { speaker: string; text: string; interrupted: boolean; spoken_text?: string | null; intent?: string }[];
  latency?: { lifecycle?: string[]; barge_ins?: unknown[] };
};
type Detail = { run: Run; results: Result[] };

// Short descriptions per category (presentation copy; counts and pass/fail always come from the run).
const BLURB: Record<string, string> = {
  identity: "Verification before any disclosure — wrong party, failed checks, partial dates of birth.",
  policy: "Envelope limits, discount requests, prompt injection and hallucinated terms.",
  japanese: "日本語 parity: promise, wrong party, stop-contact, extension, transfer.",
  audio: "Background noise, thinking pauses, short answers, silence.",
  barge_in: "Mid-sentence interrupts; a “yes” over an unfinished read-back is not consent.",
  caller_rights: "Stop-contact and human transfer, verified or not.",
  negotiation: "Partial payments and ambiguous offers.",
  correction: "Amount and date corrections during read-back.",
  promise_to_pay: "The cooperative ¥30,000 promise.",
  disconnect: "Hang-up mid-confirmation never creates a promise.",
  resilience: "Invalid LLM output falls back to rules.",
};
const SCORE_LABEL: Record<string, string> = {
  naturalness: "Naturalness",
  task_completion: "Task completion",
  policy_adherence: "Policy adherence",
  empathy_professionalism: "Empathy",
  hallucination_risk: "Hallucination risk (5 = low)",
  unnecessary_repetition: "Repetition (5 = none)",
};

const ONES = ["Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"];
const TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"];
function words(n: number) {
  if (n < 20) return ONES[n];
  if (n < 100) return TENS[Math.floor(n / 10)] + (n % 10 ? `-${ONES[n % 10].toLowerCase()}` : "");
  return String(n);
}
const LEAD =
  "Scripted regression scenarios run through the real runtime with mock providers on a virtual clock. Deterministic invariants are authoritative; judge scores are supplementary.";

const when = (iso: string) => {
  const dt = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
  return Number.isNaN(dt.getTime()) ? iso : dt.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
};

function friendlyError(msg: string) {
  if (msg.startsWith("429")) return "Rate limit reached: the suite can be run 3 times per 10 minutes from one address. The latest stored run is shown.";
  if (msg.startsWith("409")) return "An evaluation run is already in progress on the server. Try again in a few seconds.";
  if (/failed to fetch|networkerror/i.test(msg)) return "Backend not reachable. Start the API or set NEXT_PUBLIC_API_BASE_URL.";
  return msg;
}

/* ------------------------------------------------------------ ring counter */

function Counter({ passed, total, runKey, reduced }: { passed: number; total: number; runKey: string; reduced: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { once: true, margin: "-10% 0px" });
  const mv = useMotionValue(0);
  const rounded = useTransform(mv, (v) => Math.round(v));
  const [lit, setLit] = useState(0);
  useMotionValueEvent(rounded, "change", (v) => setLit(v));
  useEffect(() => {
    if (!inView) return;
    if (reduced) {
      mv.set(passed);
      return;
    }
    mv.set(0);
    const c = animate(mv, passed, { duration: 2, ease: [0.16, 1, 0.3, 1] });
    return () => c.stop();
  }, [inView, reduced, mv, passed, runKey]);
  const n = Math.max(total, 1);
  return (
    <div ref={ref} className={e.counter}>
      <svg viewBox="0 0 200 200" className={e.ticks} aria-hidden="true">
        {Array.from({ length: n }, (_, i) => {
          // Rounded so server and client serialise identical numbers.
          const a = (i / n) * Math.PI * 2 - Math.PI / 2;
          const at = (r: number, f: (x: number) => number) => Math.round((100 + f(a) * r) * 100) / 100;
          const state = i < lit ? "on" : i >= passed && lit >= passed && inView ? "fail" : "off";
          return <line key={i} x1={at(82, Math.cos)} y1={at(82, Math.sin)} x2={at(94, Math.cos)} y2={at(94, Math.sin)} data-state={state} />;
        })}
      </svg>
      <div className={e.counterNum} aria-label={`${passed} of ${total} cases passed`}>
        <m.span>{rounded}</m.span>
        <small>/{total}</small>
      </div>
      <div className={e.counterSub}>{passed === total ? "cases pass every deterministic invariant" : `${total - passed} case${total - passed === 1 ? "" : "s"} failing an invariant`}</div>
    </div>
  );
}

/* ------------------------------------------------------------ bento */

function Bento({ cats, active, onPick }: { cats: [string, { total: number; passed: number }][]; active: string | null; onPick: (k: string | null) => void }) {
  const onMove = (ev: React.PointerEvent<HTMLButtonElement>) => {
    const r = ev.currentTarget.getBoundingClientRect();
    ev.currentTarget.style.setProperty("--mx", `${ev.clientX - r.left}px`);
    ev.currentTarget.style.setProperty("--my", `${ev.clientY - r.top}px`);
  };
  // Largest categories span two columns. Pick how many so the 4-column grid has no holes ((n + wide) % 4 == 0)
  // and the 2-column layout pairs up the narrow tiles ((n - wide) even); cats arrive sorted by size.
  const n = cats.length;
  let wide = 0;
  for (let w = Math.floor(n / 2); w >= 0; w--) {
    if ((n + w) % 4 === 0 && (n - w) % 2 === 0) {
      wide = w;
      break;
    }
  }
  return (
    <div className={e.bento} role="group" aria-label="Filter cases by category">
      {cats.map(([k, v], i) => {
        const ok = v.passed === v.total;
        return (
          <m.button
            type="button"
            key={k}
            className={cx(e.tile, i < wide && e.tileWide)}
            data-ok={ok}
            aria-pressed={active === k}
            onPointerMove={onMove}
            onClick={() => onPick(active === k ? null : k)}
            initial={{ opacity: 0, y: 24 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: "-40px" }}
            transition={{ ...SPRING, delay: (i % 4) * 0.06 }}
          >
            <span className={e.tileTop}>
              <span className={e.tileName}>{humanize(k)}</span>
              <span className={e.tileScore}>
                {v.passed}/{v.total}
              </span>
            </span>
            <span className={e.dots} aria-hidden="true">
              {Array.from({ length: v.total }, (_, j) => (
                <i key={j} data-ok={j < v.passed} style={{ animationDelay: `${0.3 + j * 0.08}s` }} />
              ))}
            </span>
            {BLURB[k] && <span className={e.tileBlurb}>{BLURB[k]}</span>}
          </m.button>
        );
      })}
    </div>
  );
}

/* ------------------------------------------------------------ case list */

function CaseDetail({ r }: { r: Result }) {
  const scores = r.judge?.scores ? Object.entries(r.judge.scores) : [];
  return (
    <div className={e.detail}>
      <section>
        <h4 className={e.detailHead}>
          Invariants <span>authoritative · {r.invariants.filter((i) => i.passed).length}/{r.invariants.length}</span>
        </h4>
        <ul className={e.invs}>
          {r.invariants.map((i) => (
            <li key={i.name} data-ok={i.passed}>
              <b aria-label={i.passed ? "passed" : "failed"}>{i.passed ? "✓" : "✗"}</b>
              <span>
                <code>{i.name}</code>
                {i.detail && <em>{i.detail}</em>}
              </span>
            </li>
          ))}
        </ul>
        {scores.length > 0 && (
          <>
            <h4 className={e.detailHead}>
              Judge <span>supplementary · {r.judge?.provider === "mock" ? "heuristic, not an LLM" : r.judge?.provider}</span>
            </h4>
            <ul className={e.scores}>
              {scores.map(([k, v]) => (
                <li key={k} title={v.reason}>
                  <span>{SCORE_LABEL[k] ?? humanize(k)}</span>
                  <span className={e.bar} aria-hidden="true">
                    <i style={{ width: `${(v.score / 5) * 100}%` }} />
                  </span>
                  <b>{v.score}/5</b>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>
      <section>
        <h4 className={e.detailHead}>
          Transcript <span>{r.language === "ja" ? "日本語" : "English"} · scripted caller</span>
        </h4>
        <div className={e.mini}>
          {r.transcript.map((t, k) => (
            <div key={k} className={e.miniMsg} data-who={t.speaker}>
              <span className={e.miniWho}>
                {t.speaker}
                {t.intent ? ` · ${t.intent}` : ""}
                {t.interrupted ? " · interrupted" : ""}
              </span>
              <span>{t.interrupted && t.spoken_text ? t.spoken_text + " …" : t.text}</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function CaseList({ results }: { results: Result[] }) {
  const [open, setOpen] = useState<string | null>(null);
  const list = useRef<HTMLOListElement>(null);
  // Arrow keys move between case rows (Tab still works); Enter/Space toggle via the native button.
  const onKey = (ev: React.KeyboardEvent<HTMLOListElement>) => {
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(ev.key)) return;
    const btns = [...(list.current?.querySelectorAll<HTMLButtonElement>("button[data-case]") ?? [])];
    const idx = btns.indexOf(document.activeElement as HTMLButtonElement);
    if (idx < 0) return;
    ev.preventDefault();
    const next = ev.key === "Home" ? 0 : ev.key === "End" ? btns.length - 1 : Math.min(btns.length - 1, Math.max(0, idx + (ev.key === "ArrowDown" ? 1 : -1)));
    btns[next]?.focus();
  };
  return (
    <ol className={e.cases} ref={list} onKeyDown={onKey} aria-label="Evaluation cases">
      {results.map((r, i) => {
        const isOpen = open === r.case_key;
        const id = `case-${r.case_key}`;
        return (
          <m.li
            key={r.case_key}
            className={cx(e.caseItem, isOpen && e.caseOpen)}
            layout="position"
            initial={{ opacity: 0, y: 18, filter: "blur(6px)" }}
            whileInView={{ opacity: 1, y: 0, filter: "blur(0px)" }}
            viewport={{ once: true, margin: "-30px" }}
            transition={{ duration: 0.5, delay: (i % 6) * 0.05, ease: [0.22, 1, 0.36, 1] }}
          >
            <button type="button" className={e.caseRow} data-case={r.case_key} aria-expanded={isOpen} aria-controls={id} onClick={() => setOpen(isOpen ? null : r.case_key)}>
              <span className={e.caseIdx}>{String(i + 1).padStart(2, "0")}</span>
              <span className={e.caseMain}>
                <b>{r.title}</b>
                <code>{r.case_key}</code>
              </span>
              <span className={e.caseCat}>
                {humanize(r.category)}
                {r.language === "ja" && " · JA"}
              </span>
              <span className={r.passed ? e.pass : e.fail}>{r.passed ? "PASS" : "FAIL"}</span>
              <span className={e.chev} aria-hidden="true" />
            </button>
            <AnimatePresence initial={false}>
              {isOpen && (
                <m.div id={id} key="d" initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={{ duration: 0.3 }} style={{ overflow: "hidden" }}>
                  <CaseDetail r={r} />
                </m.div>
              )}
            </AnimatePresence>
          </m.li>
        );
      })}
    </ol>
  );
}

/* ------------------------------------------------------------ page */

export function Evaluation() {
  const reduced = usePrefersReducedMotion();
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [cat, setCat] = useState<string | null>(null);

  const load = useCallback(async (pick?: string) => {
    try {
      const r = await getJSON<Run[]>("/api/eval/runs");
      setRuns(r);
      const id = pick ?? r[0]?.id;
      setDetail(id ? await getJSON<Detail>(`/api/eval/runs/${id}`) : null);
    } catch (ex) {
      setRuns((x) => x ?? []);
      setErr(friendlyError((ex as Error).message));
    }
  }, []);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  async function run() {
    setBusy(true);
    setErr(null);
    try {
      const res = await getJSON<{ run_id: string }>("/api/eval/run", { method: "POST" });
      setCat(null);
      await load(res.run_id);
    } catch (ex) {
      setErr(friendlyError((ex as Error).message));
    } finally {
      setBusy(false);
    }
  }

  const run0 = detail?.run;
  const cats = useMemo(() => (run0 ? Object.entries(run0.summary.by_category).sort((a, b) => b[1].total - a[1].total) : []), [run0]);
  const shown = useMemo(() => (detail ? detail.results.filter((r) => !cat || r.category === cat) : []), [detail, cat]);
  const allPass = run0 ? run0.passed === run0.total : false;
  const mockJudge = run0?.judge_provider === "mock";

  return (
    <MotionRoot>
      <main className={e.page}>
        {/* Desktop: sticky left column (headline, ring, run button, category filters) | cases on the right.
            Below 1024 px both columns use display: contents and CSS order keeps the original stacked sequence. */}
        <div className={e.split}>
          <div className={e.left}>
            <m.header className={e.head} initial={{ opacity: 0, y: 20, filter: "blur(8px)" }} animate={{ opacity: 1, y: 0, filter: "blur(0px)" }} transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1] }}>
              <span className={ui.overline}>/evaluation · stored runs from the live backend</span>
              <h1 className={cx(ui.h1, e.title)}>
                {run0 ? `${words(run0.total)} calls.` : "Scripted calls."}
                <br />
                <span className={ui.grad}>{!run0 ? "Real invariants." : allPass ? "Zero broken rules." : `${words(run0.total - run0.passed)} broken.`}</span>
              </h1>
              <p className={cx(ui.lead, e.leadStacked)}>{LEAD}</p>
              {run0 && (
                <div className={e.pills}>
                  <Chip tone="mock">mock providers · virtual clock</Chip>
                  <Chip tone="mock">judge: {mockJudge ? "heuristic" : run0.judge_model}, supplementary</Chip>
                  <Chip title={run0.code_version}>code {run0.code_version.slice(0, 7)}</Chip>
                  <Chip>run {when(run0.started_at)}</Chip>
                </div>
              )}
            </m.header>

            {run0 && detail && (
              <div className={e.counterWrap}>
                <Counter passed={run0.passed} total={run0.total} runKey={run0.id} reduced={reduced} />
              </div>
            )}

            <div className={e.runBlock}>
              <div className={e.runRow}>
                <Cta onClick={() => void run()} disabled={busy} aria-busy={busy}>
                  {busy ? <span className={e.spinner} aria-hidden="true" /> : <PlayIcon />}
                  {busy ? `Running ${run0?.total ?? "all"} scenarios…` : "Run evaluation suite"}
                </Cta>
                <span className={ui.fine}>{busy ? "Takes about 10 seconds on the server." : "Runs the deterministic suite on the backend (rate-limited)."}</span>
              </div>
              {err && (
                <div className={ui.banner} role="alert">
                  {err}
                </div>
              )}
            </div>

            {run0 && detail && (
              <div className={e.bentoWrap}>
                <span className={cx(ui.overline, e.filterLabel)}>Filter by category</span>
                <Bento cats={cats} active={cat} onPick={setCat} />
              </div>
            )}
          </div>

          <div className={e.right}>
            <p className={cx(ui.lead, e.leadDesktop)}>{LEAD}</p>

            {runs === null && (
              <div className={e.loading} aria-busy="true">
                <span className={e.skelRing} />
              </div>
            )}
            {runs !== null && !run0 && !err && (
              <div className={e.empty}>
                <p>No runs stored yet on this deployment. Click “Run evaluation suite” — it takes a few seconds.</p>
              </div>
            )}

            {run0 && detail && (
              <>
                <p className={e.clock}>{run0.summary.clock}</p>
                <div className={e.casesHead}>
                  <span className={ui.overline}>
                    {cat ? `${humanize(cat)} · ${shown.length} cases` : `All ${detail.results.length} cases`} · {when(run0.started_at)}
                  </span>
                  {cat ? (
                    <button type="button" className={ui.ghostBtn} onClick={() => setCat(null)}>
                      Show all cases
                    </button>
                  ) : (
                    <span className={e.dim}>invariants decide pass/fail — judge scores never do · ↑↓ to move, Enter to open</span>
                  )}
                </div>
                <div className={e.listWrap}>
                  <CaseList results={shown} />
                </div>

                {runs && runs.length > 1 && (
                  <section className={e.history} aria-label="Run history">
                    <span className={ui.overline}>Run history</span>
                    <div className={e.historyRow}>
                      {runs.slice(0, 8).map((r) => (
                        <button key={r.id} type="button" className={e.histBtn} aria-pressed={r.id === run0.id} onClick={() => void load(r.id)}>
                          <b data-ok={r.passed === r.total}>
                            {r.passed}/{r.total}
                          </b>
                          <span>{when(r.started_at)}</span>
                          <code>{r.code_version.slice(0, 7)}</code>
                        </button>
                      ))}
                    </div>
                  </section>
                )}
              </>
            )}

            <section className={e.bench}>
              <span className={ui.overline}>Audio / provider benchmarks</span>
              <p>
                Deterministic VAD fixtures (clean, background noise, short/long pause, clicks) run in CI. The speech-provider benchmark (CER, amount/date/name accuracy on
                EN/JA fixtures) is implemented in <code>app/evaluation/audio_bench.py</code> but has <b>not been executed</b> for this deployment unless a report is
                published in <code>docs/EVALUATION.md</code>. No accuracy numbers are claimed.
              </p>
              <p className={e.dim}>Runs listed: {runs?.length ?? 0}</p>
            </section>
          </div>
        </div>
      </main>
    </MotionRoot>
  );
}

function PlayIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
      <path d="M7 4.5v15l13-7.5z" fill="currentColor" />
    </svg>
  );
}
