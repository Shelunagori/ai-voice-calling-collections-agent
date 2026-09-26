"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { SessionDetailView } from "@/components/audit/SessionDetailView";
import { getJSON } from "@/lib/api";
import { shortTime } from "@/lib/format";
import { DetailResult, loadSessionDetail } from "@/lib/session-detail";

type Row = Record<string, string | number | boolean | null>;

function List() {
  const [rows, setRows] = useState<Row[]>([]);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    getJSON<Row[]>("/api/sessions?limit=50")
      .then((r) => setRows(Array.isArray(r) ? r : []))
      .catch((e: Error) => setErr(`Could not load sessions (${e.message}).`));
  }, []);
  return (
    <div className="panel table-scroll">
      {err && (
        <div className="banner" role="alert">
          {err}
        </div>
      )}
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
              <td>
                <span className={`pill ${r.channel === "phone" ? "warn" : "info"}`}>{String(r.channel)}</span>
              </td>
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
      <p className="muted small">Browser and phone sessions both open here; phone-session detail is fetched server-side with the operator token.</p>
    </div>
  );
}

function DetailView({ id }: { id: string }) {
  const [attempt, setAttempt] = useState(0);
  const key = `${id}#${attempt}`;
  const [loaded, setLoaded] = useState<{ key: string; res: DetailResult } | null>(null);
  useEffect(() => {
    let live = true;
    void loadSessionDetail(id).then((res) => {
      if (live) setLoaded({ key, res });
    });
    return () => {
      live = false;
    };
  }, [id, key]);
  const reload = useCallback(() => setAttempt((n) => n + 1), []);
  const res = loaded?.key === key ? loaded.res : null;

  if (!res) return <div className="muted" role="status">Loading session…</div>;
  if (res.kind === "error") {
    return (
      <div className="stack">
        <div className="banner" role="alert" data-testid="detail-error" data-code={res.code}>
          {res.message} {res.status ? <span className="muted mono">(HTTP {res.status})</span> : null}
        </div>
        {res.code !== "not_found" && (
          <div>
            <button className="btn" onClick={reload}>
              Retry
            </button>
          </div>
        )}
      </div>
    );
  }
  return (
    <div className="stack">
      <div className="row">
        <button className="btn sm" onClick={reload}>
          Refresh
        </button>
      </div>
      <SessionDetailView id={id} d={res.detail} operator={res.operator} />
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
