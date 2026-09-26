"use client";

import type { ReactNode } from "react";
import {
  collectionState,
  conversation,
  decisionTone,
  endState,
  identityTimeline,
  latencySummary,
  maskDobFields,
  MIN_SAMPLES_FOR_P95,
  policyDecisions,
  voiceRuntime,
} from "@/lib/audit-summary";
import { ms, shortTime, yen } from "@/lib/format";
import type { SessionDetail } from "@/lib/session-detail";

const str = (v: unknown) => (v === null || v === undefined || v === "" ? "—" : String(v));
const money = (v: unknown) => (typeof v === "number" ? yen(v) : "—");

function Panel({ title, children, note }: { title: string; children: ReactNode; note?: string }) {
  return (
    <section className="panel" aria-label={title}>
      <div className="panel-head">
        <h3>{title}</h3>
        {note && <span className="muted small">{note}</span>}
      </div>
      {children}
    </section>
  );
}

export function SessionDetailView({ id, d, operator }: { id: string; d: SessionDetail; operator: boolean }) {
  const s = d.session;
  const phone = s.channel === "phone";
  const end = endState(s);
  const convo = conversation(d.turns);
  const identity = identityTimeline(d.audit);
  const policy = policyDecisions(d.audit);
  const voice = voiceRuntime(d.audit);
  const lat = latencySummary(d.latency);
  const coll = collectionState(d);
  const providers = (s.providers ?? {}) as Record<string, string>;

  function download() {
    const blob = new Blob([JSON.stringify(d, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `session-${id}.json`;
    a.click();
  }

  return (
    <div className="stack" data-testid="session-detail">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div className="row small">
          <span className={`pill ${phone ? "warn" : "info"}`} data-testid="channel-pill">
            {phone ? "phone (PSTN)" : String(s.channel ?? "browser")}
          </span>
          <span className="pill muted">{str(s.language)}</span>
          <span className="pill info">{str(s.identity_status)}</span>
          <span className="pill info">{str(s.promise_status)}</span>
          <span className={`pill ${end.category === "error_or_disconnect" ? "bad" : "muted"}`}>{end.category}</span>
          {operator && <span className="pill ok">operator view</span>}
        </div>
        <button className="btn sm" onClick={download}>
          Download JSON
        </button>
      </div>

      <div className="grid cols-2">
        <Panel title="Session">
          <dl className="kv">
            <dt>Session id</dt>
            <dd>{id}</dd>
            <dt>Channel</dt>
            <dd>{str(s.channel)}</dd>
            <dt>Scenario · language</dt>
            <dd>
              {str(s.scenario_key)} · {str(s.language)} · input {str(s.input_mode)}
            </dd>
            {phone && (
              <>
                <dt>Twilio call id</dt>
                <dd>{str(s.call_id)}</dd>
              </>
            )}
            <dt>Providers</dt>
            <dd>{Object.entries(providers).map(([k, v]) => `${k}=${v}`).join(" · ") || "—"}</dd>
            <dt>Started</dt>
            <dd>{str(s.started_at)}</dd>
            <dt>Ended</dt>
            <dd>{str(s.ended_at)}</dd>
            <dt>Call status</dt>
            <dd>{str(s.call_status)}</dd>
            <dt>End reason</dt>
            <dd>{str(s.ended_reason)}</dd>
          </dl>
        </Panel>

        <Panel title="Identity" note="DOB values are masked here; parts given are shown">
          {identity.length === 0 ? (
            <p className="muted small">No identity events.</p>
          ) : (
            <ol className="small" style={{ margin: 0, paddingLeft: 18 }}>
              {identity.map((i) => (
                <li key={i.id} style={{ marginBottom: 4 }}>
                  <span className={`pill ${i.tone}`}>{i.label}</span> <span className="muted mono">turn {str(i.turn)}</span> {i.detail}
                </li>
              ))}
            </ol>
          )}
        </Panel>
      </div>

      <Panel title={`Conversation (${convo.length} utterances)`}>
        <div className="stack">
          {convo.map((t) => (
            <div key={t.key} className="small" style={{ borderLeft: `3px solid var(${t.speaker === "caller" ? "--accent" : "--border"})`, paddingLeft: 8 }}>
              <div className="row">
                <b>{t.speaker}</b>
                <span className="mono muted">turn {str(t.turn)}</span>
                {t.acts && <span className="mono muted">{t.acts}</span>}
                {t.interrupted && <span className="pill warn">interrupted</span>}
                {t.actions.map((a) => (
                  <span key={a} className="pill info mono">
                    {a}
                  </span>
                ))}
                {t.source && <span className="muted mono">via {t.source}</span>}
              </div>
              <div>{t.text ?? <span className="muted">(text purged by retention policy)</span>}</div>
              {t.interrupted && t.spoken && (
                <div className="muted">
                  heard before barge-in: <q>{t.spoken}</q>
                </div>
              )}
              {t.notes.length > 0 && <div className="muted mono">{t.notes.join(" · ")}</div>}
            </div>
          ))}
        </div>
      </Panel>

      <div className="grid cols-2">
        <Panel title="Voice runtime">
          <h4 className="small muted">Barge-ins ({voice.bargeIns.length})</h4>
          {voice.bargeIns.length === 0 ? (
            <p className="muted small">None.</p>
          ) : (
            <table className="small">
              <thead>
                <tr>
                  <th>Turn</th>
                  <th>Trigger</th>
                  <th>Internal TTS cancel</th>
                  <th>Played</th>
                  <th>Interrupted text</th>
                </tr>
              </thead>
              <tbody>
                {voice.bargeIns.map((b) => (
                  <tr key={b.id}>
                    <td>{str(b.turn)}</td>
                    <td className="mono">{b.source}</td>
                    <td className="mono">{b.cancelMs === null ? "—" : `${b.cancelMs} ms`}</td>
                    <td className="mono">{b.playedS === null ? "—" : `${b.playedS} s`}</td>
                    <td>{b.interrupted}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <h4 className="small muted">Lifecycle ({voice.transitions.length} transitions)</h4>
          {voice.transitions.length === 0 ? (
            <p className="muted small">Not recorded for this session (sessions before this build).</p>
          ) : (
            <div className="mono small" style={{ maxHeight: 220, overflowY: "auto" }}>
              {voice.transitions.map((t, i) => (
                <div key={i}>
                  +{t.t_ms} ms {t.from} → {t.to} <span className="muted">({t.cause})</span>
                </div>
              ))}
            </div>
          )}
        </Panel>

        <Panel title="Latency" note={`p95 shown from n ≥ ${MIN_SAMPLES_FOR_P95}`}>
          <table className="small">
            <thead>
              <tr>
                <th>Stage</th>
                <th>n</th>
                <th>p50</th>
                <th>p95</th>
              </tr>
            </thead>
            <tbody>
              {lat.map((l) => (
                <tr key={l.key}>
                  <td>{l.label}</td>
                  <td className="mono">{l.count}</td>
                  <td className="mono">{ms(l.p50)}</td>
                  <td className="mono">{l.p95 === undefined ? (l.count ? "n too small" : "—") : ms(l.p95)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <details className="small" style={{ marginTop: 8 }}>
            <summary>Per turn</summary>
            {d.latency.map((l) => (
              <div key={`${l.turn_index}-${l.input_mode}`} className="mono">
                #{l.turn_index} {l.input_mode}:{" "}
                {Object.entries(l.stages)
                  .map(([k, v]) => `${k}=${ms(v)}`)
                  .join(" ")}
              </div>
            ))}
          </details>
        </Panel>
      </div>

      <div className="grid cols-2">
        <Panel title="Collection state">
          <dl className="kv">
            <dt>Balance</dt>
            <dd>{money(coll.outstanding_balance)}</dd>
            <dt>Minimum payment</dt>
            <dd>{money(coll.allowed_min_payment)}</dd>
            <dt>Max extension</dt>
            <dd>{str(coll.max_extension_days)} days</dd>
            <dt>Proposed</dt>
            <dd>
              {money(coll.proposed_amount)} · {str(coll.proposed_date)}
            </dd>
            <dt>Rejected proposals</dt>
            <dd>
              {(coll.rejected_proposals as Record<string, unknown>[]).length === 0
                ? "none"
                : (coll.rejected_proposals as Record<string, unknown>[])
                    .map((r) => `${money(r.amount)} ${str(r.due_date)} ✗ ${(r.violated_rules as string[] | undefined)?.join(",") ?? ""}`)
                    .join(" | ")}
            </dd>
            <dt>Promise</dt>
            <dd>
              {str(coll.promise_status)}
              {d.promise && ` · ${money(d.promise.amount)} due ${str(d.promise.due_date)} (turn ${str(d.promise.confirmation_turn)})`}
            </dd>
            <dt>Stop contact</dt>
            <dd>
              {coll.stop_contact ? "yes" : "no"} · future contact {coll.future_contact_eligible === false ? "not eligible" : "eligible"}
            </dd>
            <dt>Transfer</dt>
            <dd>
              {coll.human_transfer_requested ? `requested (${str(coll.transfer_reason)})` : "no"} · {str(coll.transfer_status)}
            </dd>
          </dl>
        </Panel>

        <Panel title="End state">
          <dl className="kv">
            <dt>Outcome</dt>
            <dd>{end.category}</dd>
            <dt>Reason</dt>
            <dd>{end.reason || "—"}</dd>
            <dt>Call status</dt>
            <dd>{end.callStatus || "—"}</dd>
          </dl>
        </Panel>
      </div>

      <Panel title={`Policy decisions (${policy.length})`}>
        <table className="small">
          <thead>
            <tr>
              <th>Turn</th>
              <th>Decision</th>
              <th>Rule</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            {policy.map((p) => (
              <tr key={p.id}>
                <td>{str(p.turn)}</td>
                <td>
                  <span className={`pill ${decisionTone(p.decision)}`}>{p.decision}</span>
                </td>
                <td className="mono">{p.rule}</td>
                <td>
                  {p.reason}
                  {p.details && <span className="muted mono"> {JSON.stringify(p.details)}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>

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
                      <span className={`pill ${decisionTone(String(e.data.decision))}`}>{String(e.data.decision)}</span> {String(e.data.rule)}
                    </>
                  ) : (
                    e.type
                  )}
                </td>
                <td className="mono small" style={{ maxWidth: 520, overflowWrap: "anywhere" }}>
                  {e.type === "policy.decision"
                    ? String(e.data.reason)
                    : e.type === "voice.lifecycle"
                      ? `${voice.transitions.length} transitions (see Voice runtime)`
                      : JSON.stringify(maskDobFields(e.data ?? {}, e.type))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
