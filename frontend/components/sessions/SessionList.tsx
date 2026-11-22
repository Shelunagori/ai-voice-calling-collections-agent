"use client";

import { m } from "framer-motion";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Chip } from "@/components/ui/Chip";
import { CtaLink, MicIcon } from "@/components/ui/Cta";
import { cx } from "@/components/ui/cx";
import ui from "@/components/ui/ui.module.css";
import { getJSON, type Scenario } from "@/lib/api";
import { callLabel, channelLabel, fmtDuration, fmtWhen, identityLabel, languageLabel, promiseLabel, reasonLabel } from "./labels";
import s from "./sessions.module.css";

type Row = Record<string, string | number | boolean | null>;

export function SessionList() {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [titles, setTitles] = useState<Record<string, string>>({});
  const [channel, setChannel] = useState<"all" | "browser" | "phone">("all");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let live = true;
    getJSON<Row[]>("/api/sessions?limit=50")
      .then((r) => {
        if (!live) return;
        setRows(Array.isArray(r) ? r : []);
        setErr(null);
      })
      .catch((e: Error) => {
        if (!live) return;
        setRows([]);
        setErr(`Could not load sessions (${e.message}).`);
      });
    return () => {
      live = false;
    };
  }, [attempt]);
  useEffect(() => {
    getJSON<Scenario[]>("/api/scenarios")
      .then((list) => setTitles(Object.fromEntries(list.map((x) => [x.key, x.title]))))
      .catch(() => setTitles({}));
  }, []);
  const retry = useCallback(() => {
    setRows(null);
    setAttempt((n) => n + 1);
  }, []);

  const shown = useMemo(() => (rows ?? []).filter((r) => channel === "all" || r.channel === channel), [rows, channel]);
  const stats = useMemo(() => {
    const all = rows ?? [];
    return {
      total: all.length,
      promises: all.filter((r) => r.promise_status === "CONFIRMED").length,
      stops: all.filter((r) => r.stop_contact).length,
      transfers: all.filter((r) => r.human_transfer_requested).length,
    };
  }, [rows]);

  return (
    <>
      <div className={s.stats} aria-label="Latest sessions at a glance">
        {[
          ["Sessions", stats.total, "plain"],
          ["Promises confirmed", stats.promises, "ok"],
          ["Stop-contact", stats.stops, "warn"],
          ["Human transfer", stats.transfers, "info"],
        ].map(([label, n, tone]) => (
          <div key={String(label)} className={s.stat} data-tone={tone}>
            <b>{rows === null ? "–" : n}</b>
            <span>{label}</span>
          </div>
        ))}
      </div>

      <div className={s.listBar}>
        <span className={ui.overline}>Latest {rows?.length ?? ""} sessions</span>
        <div className={ui.segmented} role="group" aria-label="Filter by channel">
          {(
            [
              ["all", "All"],
              ["browser", "Browser"],
              ["phone", "Phone"],
            ] as const
          ).map(([k, label]) => (
            <button key={k} type="button" aria-pressed={channel === k} onClick={() => setChannel(k)}>
              {label}
            </button>
          ))}
        </div>
      </div>

      {err && (
        <div className={s.errorRow}>
          <div className={cx(ui.banner, ui.bannerBad)} role="alert">
            {err}
          </div>
          <button type="button" className={ui.ghostBtn} onClick={retry}>
            Retry
          </button>
        </div>
      )}

      <div className={s.table}>
        <div className={s.headRow} aria-hidden="true">
          {["Started", "Scenario", "Channel", "Identity", "Outcome", "Duration"].map((h) => (
            <span key={h}>{h}</span>
          ))}
        </div>
        {rows === null &&
          Array.from({ length: 6 }, (_, i) => (
            <div key={i} className={s.skelRow} aria-hidden="true">
              <i style={{ width: "70%" }} />
              <i style={{ width: "85%" }} />
              <i style={{ width: "55%" }} />
              <i style={{ width: "60%" }} />
              <i style={{ width: "75%" }} />
              <i style={{ width: "40%" }} />
            </div>
          ))}
        {rows === null && (
          <span className={s.srOnly} role="status">
            Loading sessions…
          </span>
        )}
        <ul className={s.rows} aria-label="Sessions">
        {shown.map((r, i) => {
          const [idText, idTone] = identityLabel(r.identity_status);
          const [prText, prTone] = promiseLabel(r.promise_status);
          const [callText] = callLabel(r.call_status);
          return (
            <m.li key={String(r.id)} initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4, delay: Math.min(i, 12) * 0.03 }}>
              <Link href={`/sessions?id=${r.id}`} className={s.row}>
                <span className={s.when}>
                  <b>{fmtWhen(r.started_at)}</b>
                  <code>{String(r.id).slice(0, 8)}</code>
                </span>
                <span className={s.scn}>
                  <em>{String(r.scenario_key ?? "?")}</em>
                  <span>
                    {titles[String(r.scenario_key)] ?? "Scenario"}
                    <small>{languageLabel(r.language)}</small>
                  </span>
                </span>
                <span>
                  <Chip tone={r.channel === "phone" ? "warn" : "info"}>{channelLabel(r.channel)}</Chip>
                </span>
                <span>
                  <Chip tone={idTone} dot title={String(r.identity_status)}>
                    {idText}
                  </Chip>
                </span>
                <span className={s.outcome}>
                  <Chip tone={prTone} title={String(r.promise_status)}>
                    {prText}
                  </Chip>
                  {r.stop_contact && <Chip tone="warn">Stop-contact</Chip>}
                  {r.human_transfer_requested && <Chip tone="info">Transfer</Chip>}
                  <small title={`${String(r.call_status)} · ${String(r.ended_reason ?? "")}`}>
                    {callText}
                    {r.ended_reason ? ` · ${reasonLabel(r.ended_reason)}` : ""}
                  </small>
                </span>
                <span className={s.dur}>
                  {fmtDuration(r.started_at, r.ended_at)}
                  <span className={s.go} aria-hidden="true">
                    →
                  </span>
                </span>
              </Link>
            </m.li>
          );
        })}
        </ul>
      </div>

      {rows !== null && shown.length === 0 && !err && (
        <div className={s.empty}>
          <span className={s.emptyOrb} aria-hidden="true" />
          <h2 className={ui.h3}>{rows.length === 0 ? "No sessions yet" : `No ${channel} sessions in the latest ${rows.length}`}</h2>
          <p>{rows.length === 0 ? "Start a call in the voice demo — its full audit trail appears here the moment it ends." : "Switch the filter to see the others."}</p>
          {rows.length === 0 && (
            <CtaLink href="/demo">
              <MicIcon />
              Start a voice call
            </CtaLink>
          )}
        </div>
      )}

      <p className={s.note}>Browser and phone sessions both open here; phone-session detail is fetched server-side with the operator token.</p>
    </>
  );
}
