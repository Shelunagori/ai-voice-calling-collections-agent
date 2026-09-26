"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { getJSON } from "@/lib/api";
import { ms, shortTime, yen } from "@/lib/format";

type Row = Record<string, string | number | boolean | null>;
type Detail = {
  session: Row & { state: Record<string, unknown>; providers: Record<string, string> };
  turns: { seq: number; speaker: string; text: string | null; interrupted: boolean; acts: string | null; spoken_text: string | null }[];
  audit: { id: string; type: string; at: string; turn_index: number | null; data: Record<string, unknown> }[];
  promise: Row | null;
  latency: { turn_index: number; input_mode: string; provider_mode: string; stages: Record<string, number> }[];
};

function List() {
  const [rows, setRows] = useState<Row[]>([]);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    getJSON<Row[]>("/api/sessions?limit=50").then(setRows).catch((e: Error) => setErr(e.message));
  }, []);
  return (
    <div className="panel table-scroll">
      {err && <div className="banner">{err}</div>}
      <table>
        <thead>
          <tr>
            <th>Started</th>
            <th>Scenario</th>
            <th>Channel</th>
            <th>Identity</th>
            <th>Promise</th>
            <th>Stop</th>
            <th>Transfer</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={String(r.id)}>
              <td>
                <Link href={`/sessions?id=${r.id}`}>{shortTime(String(r.started_at))}</Link>
              </td>
              <td>
                {String(r.scenario_key)} · {String(r.language)}
              </td>
              <td>{String(r.channel)}</td>
              <td>{String(r.identity_status)}</td>
              <td>{String(r.promise_status)}</td>
              <td>{r.stop_contact ? "yes" : ""}</td>
              <td>{r.human_transfer_requested ? "yes" : ""}</td>
              <td className="small">
                {String(r.call_status)} {r.ended_reason ? `· ${r.ended_reason}` : ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length === 0 && !err && <p className="muted small">No sessions yet — start one in the voice demo.</p>}
    </div>
  );
}

function DetailView({ id }: { id: string }) {
  const [d, setD] = useState<Detail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    getJSON<Detail>(`/api/sessions/${id}`).then(setD).catch((e: Error) => setErr(e.message));
  }, [id]);
  if (err) return <div className="banner">{err}</div>;
  if (!d) return <div className="muted">Loading…</div>;
  const s = d.session;
  function download() {
    const blob = new Blob([JSON.stringify(d, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `session-${id}.json`;
    a.click();
  }
  return (
    <div className="stack">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div className="row small">
          <span className="mono">{id}</span>
          <span className="pill muted">{String(s.channel)}</span>
          <span className="pill info">{String(s.identity_status)}</span>
          <span className="pill info">{String(s.promise_status)}</span>
          <span className="pill muted">{String(s.call_status)}</span>
        </div>
        <button className="btn sm" onClick={download}>
          Download JSON
        </button>
      </div>
      <div className="grid cols-2">
        <div className="panel">
          <h3>Transcript</h3>
          {d.turns.map((t) => (
            <div key={`${t.speaker}${t.seq}`} className="small" style={{ marginBottom: 6 }}>
              <b>{t.speaker}</b> <span className="mono muted">{t.acts}</span>
              {t.interrupted && <span className="pill warn">interrupted</span>}
              <div>{t.text ?? <span className="muted">(text purged by retention policy)</span>}</div>
            </div>
          ))}
        </div>
        <div className="stack">
          <div className="panel">
            <h3>Promise-to-pay</h3>
            {d.promise ? (
              <dl className="kv">
                <dt>Amount</dt>
                <dd>{yen(Number(d.promise.amount))}</dd>
                <dt>Due</dt>
                <dd>{String(d.promise.due_date)}</dd>
                <dt>Confirmation turn</dt>
                <dd>{String(d.promise.confirmation_turn)}</dd>
                <dt>Confirmed at</dt>
                <dd>{String(d.promise.confirmed_at)}</dd>
              </dl>
            ) : (
              <p className="muted small">No confirmed promise.</p>
            )}
          </div>
          <div className="panel">
            <h3>Latency per turn</h3>
            {d.latency.map((l) => (
              <div key={l.turn_index + l.input_mode} className="small mono">
                #{l.turn_index} {l.input_mode}: first audio {ms(l.stages.speech_end_to_first_audio ?? l.stages.input_to_first_audio ?? l.stages.turn_commit_to_first_audio)}{" "}
                <span className="muted">({l.provider_mode})</span>
              </div>
            ))}
          </div>
        </div>
      </div>
      <div className="panel table-scroll">
        <h3>Audit trail ({d.audit.length} events)</h3>
        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>Turn</th>
              <th>Event</th>
              <th>Data</th>
            </tr>
          </thead>
          <tbody>
            {d.audit.map((e) => (
              <tr key={e.id}>
                <td className="mono small">{shortTime(e.at)}</td>
                <td>{e.turn_index ?? ""}</td>
                <td className="mono small">
                  {e.type === "policy.decision" ? (
                    <>
                      <span className={`pill ${e.data.decision === "BLOCK" ? "bad" : e.data.decision === "ALLOW" ? "ok" : "muted"}`}>{String(e.data.decision)}</span> {String(e.data.rule)}
                    </>
                  ) : (
                    e.type
                  )}
                </td>
                <td className="mono small" style={{ maxWidth: 520, overflowWrap: "anywhere" }}>
                  {e.type === "policy.decision" ? String(e.data.reason) : JSON.stringify(e.data)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function SessionsInner() {
  const q = useSearchParams();
  const id = q.get("id");
  return (
    <main className="wrap stack">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1 style={{ fontSize: 22 }}>{id ? "Session audit trail" : "Sessions"}</h1>
        {id && <Link href="/sessions">← all sessions</Link>}
      </div>
      {id ? <DetailView id={id} /> : <List />}
    </main>
  );
}

export default function SessionsPage() {
  return (
    <Suspense fallback={<main className="wrap muted">Loading…</main>}>
      <SessionsInner />
    </Suspense>
  );
}
