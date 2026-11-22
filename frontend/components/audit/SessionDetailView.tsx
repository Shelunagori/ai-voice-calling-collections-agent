"use client";

// Reviewer-facing audit view for one session (browser and phone share this layout). Readable labels up front, raw
// audited values kept in the key/value panels and the JSON export. DOB values are masked on screen.
import { useMemo, useState, type ReactNode } from "react";
import { familyOf, fmtDuration, identityLabel, languageLabel, promiseLabel, reasonLabel, endLabel, type Family } from "@/components/sessions/labels";
import s from "@/components/sessions/sessions.module.css";
import { Chip, type ChipTone } from "@/components/ui/Chip";
import { cx } from "@/components/ui/cx";
import ui from "@/components/ui/ui.module.css";
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
  type Tone,
} from "@/lib/audit-summary";
import { humanize, ms, shortTime, yen } from "@/lib/format";
import type { SessionDetail } from "@/lib/session-detail";

const str = (v: unknown) => (v === null || v === undefined || v === "" ? "—" : String(v));
const money = (v: unknown) => (typeof v === "number" ? yen(v) : "—");
const chipTone = (t: Tone): ChipTone => (t === "muted" ? "plain" : t);

function Panel({ title, children, note, wide, className }: { title: string; children: ReactNode; note?: string; wide?: boolean; className?: string }) {
  return (
    <section className={cx(s.panel, wide && s.panelWide, className)} aria-label={title}>
      <div className={s.panelHead}>
        <h3>{title}</h3>
        {note && <span>{note}</span>}
      </div>
      {children}
    </section>
  );
}

const FAMILY_LABEL: Record<Family, string> = { policy: "Policy", identity: "Identity", payment: "Payment", voice: "Voice", session: "Session", other: "Other" };

function eventTitle(type: string) {
  const [head, ...rest] = type.split(".");
  return rest.length ? `${humanize(head)} · ${rest.join(" ").replace(/_/g, " ")}` : humanize(type);
}

function DataChips({ data }: { data: Record<string, unknown> }) {
  const entries = Object.entries(data).filter(([, v]) => v !== null && v !== undefined && v !== "");
  if (!entries.length) return null;
  return (
    <span className={s.kvChips}>
      {entries.slice(0, 8).map(([k, v]) => {
        const text = typeof v === "object" ? JSON.stringify(v) : String(v);
        return (
          <span key={k} title={text}>
            <em>{k}</em> {text.length > 60 ? `${text.slice(0, 57)}…` : text}
          </span>
        );
      })}
      {entries.length > 8 && <span>+{entries.length - 8} more</span>}
    </span>
  );
}

export function SessionDetailView({ id, d, operator, onRefresh }: { id: string; d: SessionDetail; operator: boolean; onRefresh?: () => void }) {
  const sess = d.session;
  const phone = sess.channel === "phone";
  const end = endState(sess);
  const convo = conversation(d.turns);
  const identity = identityTimeline(d.audit);
  const policy = policyDecisions(d.audit);
  const voice = voiceRuntime(d.audit);
  const lat = latencySummary(d.latency);
  const coll = collectionState(d);
  const providers = (sess.providers ?? {}) as Record<string, string>;
  const [family, setFamily] = useState<Family | "all">("all");

  const [endText, endTone] = endLabel(end.category);
  const [idText, idTone] = identityLabel(sess.identity_status);
  const [prText, prTone] = promiseLabel(sess.promise_status);
  const blocks = policy.filter((p) => p.decision === "BLOCK").length;
  const fastest = voice.bargeIns.reduce<number | null>((m, b) => (b.cancelMs === null ? m : m === null ? b.cancelMs : Math.min(m, b.cancelMs)), null);
  const maxP50 = Math.max(1, ...lat.map((l) => l.p50 ?? 0));
  const families = useMemo(() => {
    const c: Partial<Record<Family, number>> = {};
    d.audit.forEach((e) => {
      const f = familyOf(e.type);
      c[f] = (c[f] ?? 0) + 1;
    });
    return c;
  }, [d.audit]);
  const trail = family === "all" ? d.audit : d.audit.filter((e) => familyOf(e.type) === family);

  function download() {
    const blob = new Blob([JSON.stringify(d, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `session-${id}.json`;
    a.click();
  }

  return (
    <div className={s.detail} data-testid="session-detail">
      <div className={s.detailBar}>
        <div className={s.chipRow}>
          <Chip tone={phone ? "warn" : "info"} data-testid="channel-pill">
            {phone ? "phone (PSTN)" : String(sess.channel ?? "browser")}
          </Chip>
          <Chip>{languageLabel(sess.language)}</Chip>
          <Chip tone={idTone} dot title={str(sess.identity_status)}>
            {idText}
          </Chip>
          <Chip tone={prTone} title={str(sess.promise_status)}>
            {prText}
          </Chip>
          {operator && (
            <Chip tone="ok" dot>
              operator view
            </Chip>
          )}
        </div>
        <div className={s.chipRow}>
          {onRefresh && (
            <button type="button" className={ui.ghostBtn} onClick={onRefresh}>
              Refresh
            </button>
          )}
          <button type="button" className={ui.ghostBtn} onClick={download}>
            Download JSON
          </button>
        </div>
      </div>

      <div className={s.tiles}>
        <div className={s.tile} data-tone={endTone}>
          <span>Outcome</span>
          <b>{endText}</b>
        </div>
        <div className={s.tile}>
          <span>Duration</span>
          <b>{fmtDuration(sess.started_at, sess.ended_at)}</b>
        </div>
        <div className={s.tile}>
          <span>Utterances</span>
          <b>{convo.length}</b>
        </div>
        <div className={s.tile} data-tone={blocks ? "warn" : undefined}>
          <span>Policy allow / block</span>
          <b>
            {policy.length - blocks} / {blocks}
          </b>
        </div>
        <div className={s.tile}>
          <span>Barge-ins</span>
          <b>
            {voice.bargeIns.length}
            {fastest !== null && <small> · {fastest} ms</small>}
          </b>
        </div>
        <div className={s.tile} data-tone={d.promise ? "ok" : undefined}>
          <span>Promise</span>
          <b>{d.promise ? money(d.promise.amount) : "—"}</b>
        </div>
      </div>

      <div className={s.grid}>
        <Panel title="Session">
          <dl className={s.kv}>
            <dt>Session id</dt>
            <dd>{id}</dd>
            <dt>Channel</dt>
            <dd>{str(sess.channel)}</dd>
            <dt>Scenario · language</dt>
            <dd>
              {str(sess.scenario_key)} · {str(sess.language)} · input {str(sess.input_mode)}
            </dd>
            {phone && (
              <>
                <dt>Twilio call id</dt>
                <dd>{str(sess.call_id)}</dd>
              </>
            )}
            <dt>Providers</dt>
            <dd>{Object.entries(providers).map(([k, v]) => `${k}=${v}`).join(" · ") || "—"}</dd>
            <dt>Started</dt>
            <dd>{str(sess.started_at)}</dd>
            <dt>Ended</dt>
            <dd>{str(sess.ended_at)}</dd>
            <dt>Call status</dt>
            <dd>{str(sess.call_status)}</dd>
            <dt>End reason</dt>
            <dd>{str(sess.ended_reason)}</dd>
          </dl>
        </Panel>

        <Panel title="Identity" note="DOB values are masked here; parts given are shown">
          {identity.length === 0 ? (
            <p className={s.muted}>No identity events.</p>
          ) : (
            <ol className={s.steps}>
              {identity.map((i) => (
                <li key={i.id} data-tone={i.tone}>
                  <Chip tone={chipTone(i.tone)}>{i.label}</Chip>
                  <span className={s.stepMeta}>turn {str(i.turn)}</span>
                  {i.detail && <span className={s.stepDetail}>{i.detail}</span>}
                </li>
              ))}
            </ol>
          )}
        </Panel>

        <Panel title={`Conversation (${convo.length} utterances)`} wide>
          <div className={s.convo}>
            {convo.map((t) => (
              <div key={t.key} className={s.utt} data-who={t.speaker}>
                <div className={s.uttMeta}>
                  <i />
                  {t.speaker}
                  <span>turn {str(t.turn)}</span>
                  {t.acts && <span>{t.acts}</span>}
                  {t.source && <span>via {t.source}</span>}
                </div>
                <div className={cx(s.uttBubble, t.interrupted && s.uttCut)}>{t.text ?? <span className={s.muted}>(text purged by retention policy)</span>}</div>
                {t.interrupted && (
                  <div className={s.uttNote}>
                    <Chip tone="bad">interrupted</Chip>
                    {t.spoken && (
                      <span>
                        heard before barge-in: <q>{t.spoken}</q>
                      </span>
                    )}
                  </div>
                )}
                {t.actions.length > 0 && (
                  <div className={s.uttActions}>
                    {t.actions.map((a) => (
                      <span key={a}>{a}</span>
                    ))}
                  </div>
                )}
                {t.notes.length > 0 && <div className={s.uttNotes}>{t.notes.join(" · ")}</div>}
              </div>
            ))}
          </div>
        </Panel>

        <Panel title="Voice runtime">
          <h4 className={s.sub}>Barge-ins ({voice.bargeIns.length})</h4>
          {voice.bargeIns.length === 0 ? (
            <p className={s.muted}>None.</p>
          ) : (
            <ul className={s.barges}>
              {voice.bargeIns.map((b) => (
                <li key={b.id}>
                  <b>{b.cancelMs === null ? "—" : `${b.cancelMs} ms`}</b>
                  <span>
                    turn {str(b.turn)} · {b.source} · played {b.playedS === null ? "—" : `${b.playedS} s`}
                    {b.interrupted && <em>{b.interrupted}</em>}
                  </span>
                </li>
              ))}
            </ul>
          )}
          <h4 className={s.sub}>Lifecycle ({voice.transitions.length} transitions)</h4>
          {voice.transitions.length === 0 ? (
            <p className={s.muted}>Not recorded for this session (sessions before this build).</p>
          ) : (
            <div className={s.transitions}>
              {voice.transitions.map((t, i) => (
                <div key={i}>
                  <span>+{t.t_ms} ms</span> {t.from} → {t.to} <em>({t.cause})</em>
                </div>
              ))}
            </div>
          )}
        </Panel>

        <Panel title="Latency" note={`p95 shown from n ≥ ${MIN_SAMPLES_FOR_P95}`}>
          <ul className={s.lat}>
            {lat.map((l) => (
              <li key={l.key}>
                <span className={s.latLabel}>{l.label}</span>
                <span className={s.latBar} aria-hidden="true">
                  <i style={{ width: `${((l.p50 ?? 0) / maxP50) * 100}%` }} />
                </span>
                <span className={s.latNums}>
                  p50 {ms(l.p50)} · p95 {l.p95 === undefined ? (l.count ? "n too small" : "—") : ms(l.p95)} · n {l.count}
                </span>
              </li>
            ))}
          </ul>
          <details className={s.more}>
            <summary>Per turn</summary>
            {d.latency.map((l) => (
              <div key={`${l.turn_index}-${l.input_mode}`}>
                #{l.turn_index} {l.input_mode}:{" "}
                {Object.entries(l.stages)
                  .map(([k, v]) => `${k}=${ms(v)}`)
                  .join(" ")}
              </div>
            ))}
          </details>
        </Panel>

        <Panel title="Collection state">
          <dl className={s.kv}>
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
          <div className={s.endHero} data-tone={endTone}>
            <b>{endText}</b>
            <span>{reasonLabel(end.reason)}</span>
          </div>
          <dl className={s.kv}>
            <dt>Outcome</dt>
            <dd>{end.category}</dd>
            <dt>Reason</dt>
            <dd>{end.reason || "—"}</dd>
            <dt>Call status</dt>
            <dd>{end.callStatus || "—"}</dd>
          </dl>
        </Panel>

        <Panel title={`Policy decisions (${policy.length})`} wide note="Simulated demo policy — not legal requirements">
          <ul className={s.policy}>
            {policy.map((p) => (
              <li key={p.id} data-decision={p.decision}>
                <Chip tone={chipTone(decisionTone(p.decision))}>{p.decision === "NOT_APPLICABLE" ? "N/A" : p.decision}</Chip>
                <span className={s.policyBody}>
                  <code>{p.rule}</code>
                  <span>
                    {p.reason}
                    {p.details && <em> {JSON.stringify(p.details)}</em>}
                  </span>
                </span>
                <span className={s.stepMeta}>turn {str(p.turn)}</span>
              </li>
            ))}
          </ul>
        </Panel>

        <section className={cx(s.panel, s.panelWide)} aria-label="Audit trail">
          <div className={s.panelHead}>
            <h3>Audit trail ({d.audit.length} events)</h3>
            <div className={s.famFilter} role="group" aria-label="Filter audit events">
              <button type="button" aria-pressed={family === "all"} onClick={() => setFamily("all")}>
                All <b>{d.audit.length}</b>
              </button>
              {(Object.keys(FAMILY_LABEL) as Family[])
                .filter((f) => families[f])
                .map((f) => (
                  <button key={f} type="button" data-family={f} aria-pressed={family === f} onClick={() => setFamily(f)}>
                    {FAMILY_LABEL[f]} <b>{families[f]}</b>
                  </button>
                ))}
            </div>
          </div>
          <ol className={s.trail}>
            {trail.map((e) => {
              const f = familyOf(e.type);
              const isPolicy = e.type === "policy.decision";
              return (
                <li key={e.id} data-family={f}>
                  <span className={s.trailTime}>{shortTime(e.at)}</span>
                  <span className={s.trailDot} aria-hidden="true" />
                  <div className={s.trailBody}>
                    <div className={s.trailHead}>
                      {isPolicy ? (
                        <>
                          <Chip tone={chipTone(decisionTone(String(e.data.decision)))}>{String(e.data.decision)}</Chip>
                          <code>{String(e.data.rule)}</code>
                        </>
                      ) : (
                        <b title={e.type}>{eventTitle(e.type)}</b>
                      )}
                      {e.turn_index !== null && e.turn_index !== undefined && <span className={s.stepMeta}>turn {e.turn_index}</span>}
                    </div>
                    {isPolicy ? (
                      <span className={s.trailText}>{String(e.data.reason)}</span>
                    ) : e.type === "voice.lifecycle" ? (
                      <span className={s.trailText}>{voice.transitions.length} transitions (see Voice runtime)</span>
                    ) : (
                      <DataChips data={maskDobFields(e.data ?? {}, e.type)} />
                    )}
                  </div>
                </li>
              );
            })}
          </ol>
        </section>
      </div>
    </div>
  );
}
