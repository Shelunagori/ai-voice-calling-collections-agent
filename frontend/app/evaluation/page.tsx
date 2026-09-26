"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import { getJSON } from "@/lib/api";

type Run = { id: string; started_at: string; total: number; passed: number; judge_provider: string; judge_model: string; judge_prompt_version: string; code_version: string; summary: { by_category: Record<string, { total: number; passed: number }>; clock: string; judge_note: string } };
type Inv = { name: string; passed: boolean; detail: string };
type Result = { case_key: string; title: string; category: string; language: string; passed: boolean; invariants: Inv[]; judge: { scores: Record<string, { score: number; reason: string }> | null; provider: string } | null; transcript: { speaker: string; text: string; interrupted: boolean }[] };

const SHORT: Record<string, string> = {
  naturalness: "nat",
  task_completion: "task",
  policy_adherence: "policy",
  empathy_professionalism: "empathy",
  hallucination_risk: "halluc",
  unnecessary_repetition: "repeat",
};

export default function EvaluationPage() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [detail, setDetail] = useState<{ run: Run; results: Result[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await getJSON<Run[]>("/api/eval/runs");
      setRuns(r);
      if (r[0]) setDetail(await getJSON(`/api/eval/runs/${r[0].id}`));
    } catch (e) {
      setErr((e as Error).message);
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
      await getJSON("/api/eval/run", { method: "POST" });
      await load();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const run0 = detail?.run;
  return (
    <main className="wrap stack">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div>
          <h1 style={{ fontSize: 22 }}>Evaluation</h1>
          <p className="muted small" style={{ margin: 0 }}>
            Scripted regression scenarios run through the real runtime with mock providers on a virtual clock. Deterministic invariants are authoritative; judge
            scores are supplementary.
          </p>
        </div>
        <button className="btn primary" onClick={run} disabled={busy}>
          {busy ? "Running…" : "Run evaluation suite"}
        </button>
      </div>
      {err && <div className="banner">{err}</div>}
      {!run0 && !err && <div className="panel muted">No runs stored yet on this deployment. Click “Run evaluation suite” (takes a few seconds).</div>}
      {run0 && (
        <>
          <div className="grid cols-4">
            <div className="panel">
              <h3>Result</h3>
              <div style={{ fontSize: 26, fontWeight: 650 }}>
                {run0.passed}/{run0.total}
              </div>
              <div className="small muted">cases passed · code {run0.code_version}</div>
            </div>
            <div className="panel">
              <h3>Judge</h3>
              <div className="small">
                {run0.judge_provider} · {run0.judge_model} · {run0.judge_prompt_version}
              </div>
              <div className="small muted">{run0.summary.judge_note}</div>
            </div>
            <div className="panel" style={{ gridColumn: "span 2" }}>
              <h3>By category</h3>
              <div className="chips">
                {Object.entries(run0.summary.by_category).map(([k, v]) => (
                  <span key={k} className={`pill ${v.passed === v.total ? "ok" : "bad"}`}>
                    {k} {v.passed}/{v.total}
                  </span>
                ))}
              </div>
              <div className="small muted" style={{ marginTop: 6 }}>
                {run0.summary.clock}
              </div>
            </div>
          </div>
          <div className="panel table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Case</th>
                  <th>Category</th>
                  <th>Lang</th>
                  <th>Invariants</th>
                  <th>Judge (supplementary)</th>
                </tr>
              </thead>
              <tbody>
                {detail!.results.map((r) => (
                  <Fragment key={r.case_key}>
                    <tr onClick={() => setOpen(open === r.case_key ? null : r.case_key)} style={{ cursor: "pointer" }}>
                      <td>
                        <span className={`pill ${r.passed ? "ok" : "bad"}`}>{r.passed ? "PASS" : "FAIL"}</span> {r.title}
                        <div className="mono small muted">{r.case_key}</div>
                      </td>
                      <td>{r.category}</td>
                      <td>{r.language}</td>
                      <td className="small">
                        {r.invariants.filter((i) => i.passed).length}/{r.invariants.length}
                      </td>
                      <td className="small mono">
                        {r.judge?.scores
                          ? Object.entries(r.judge.scores)
                              .map(([k, v]) => `${SHORT[k] ?? k}:${v.score}`)
                              .join(" ")
                          : "—"}
                      </td>
                    </tr>
                    {open === r.case_key && (
                      <tr>
                        <td colSpan={5}>
                          <div className="grid cols-2">
                            <div>
                              {r.invariants.map((i) => (
                                <div key={i.name} className="small">
                                  <span className={`pill ${i.passed ? "ok" : "bad"}`}>{i.passed ? "✓" : "✗"}</span> <span className="mono">{i.name}</span>{" "}
                                  <span className="muted">{i.detail}</span>
                                </div>
                              ))}
                            </div>
                            <div className="small">
                              {r.transcript.map((t, k) => (
                                <div key={k}>
                                  <b>{t.speaker}</b>
                                  {t.interrupted ? " (interrupted)" : ""}: {t.text}
                                </div>
                              ))}
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      <div className="panel small">
        <h3>Audio / provider benchmarks</h3>
        <p>
          Deterministic VAD fixtures (clean, background noise, short/long pause, clicks) run in CI. The speech-provider benchmark (CER, amount/date/name accuracy on EN/JA
          fixtures) is implemented in <code>app/evaluation/audio_bench.py</code> but has <b>not been executed</b> for this deployment unless a report is published in{" "}
          <code>docs/EVALUATION.md</code>. No accuracy numbers are claimed.
        </p>
        <p className="muted" style={{ margin: 0 }}>
          Runs listed: {runs.length}
        </p>
      </div>
    </main>
  );
}
