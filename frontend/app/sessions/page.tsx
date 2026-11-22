"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { SessionDetailView } from "@/components/audit/SessionDetailView";
import { SessionList } from "@/components/sessions/SessionList";
import s from "@/components/sessions/sessions.module.css";
import { cx } from "@/components/ui/cx";
import { MotionRoot } from "@/components/ui/motion";
import ui from "@/components/ui/ui.module.css";
import { DetailResult, loadSessionDetail } from "@/lib/session-detail";

function DetailSkeleton() {
  return (
    <div className={s.detail} aria-busy="true">
      <span className={s.srOnly} role="status">
        Loading session…
      </span>
      <div className={s.tiles} aria-hidden="true">
        {Array.from({ length: 6 }, (_, i) => (
          <div key={i} className={cx(s.tile, s.skelBlock)} />
        ))}
      </div>
      <div className={s.grid} aria-hidden="true">
        {Array.from({ length: 4 }, (_, i) => (
          <div key={i} className={cx(s.panel, s.skelBlock)} style={{ minHeight: 220 }} />
        ))}
      </div>
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

  if (!res) return <DetailSkeleton />;
  if (res.kind === "error") {
    return (
      <div className={s.errorCard}>
        <div className={cx(ui.banner, ui.bannerBad)} role="alert" data-testid="detail-error" data-code={res.code}>
          <span>
            {res.message} {res.status ? <span className={ui.mono}>(HTTP {res.status})</span> : null}
          </span>
        </div>
        <div className={s.errorActions}>
          {res.code !== "not_found" && (
            <button type="button" className={ui.ghostBtn} onClick={reload}>
              Retry
            </button>
          )}
          <Link className={ui.ghostBtn} href="/sessions">
            All sessions
          </Link>
        </div>
      </div>
    );
  }
  return <SessionDetailView id={id} d={res.detail} operator={res.operator} onRefresh={reload} />;
}

function SessionsInner() {
  const q = useSearchParams();
  const id = q.get("id");
  return (
    <main className={s.page}>
      <header className={cx(s.head, id && s.headDetail)}>
        {id && (
          <Link href="/sessions" className={s.back}>
            ← All sessions
          </Link>
        )}
        <span className={ui.overline}>{id ? `/sessions · ${id.slice(0, 8)}` : "/sessions & audit"}</span>
        <h1 className={id ? s.detailTitle : ui.h1}>
          {id ? (
            <>
              Session <span className={ui.grad}>audit trail</span>
            </>
          ) : (
            <>
              Every call.
              <br />
              <span className={ui.grad}>Fully reconstructable.</span>
            </>
          )}
        </h1>
        {!id && (
          <p className={ui.lead}>Every policy decision, state change, barge-in and provider failure is recorded per session. Synthetic identities and accounts only.</p>
        )}
      </header>
      {id ? <DetailView id={id} /> : <SessionList />}
    </main>
  );
}

export default function SessionsPage() {
  return (
    <MotionRoot>
      <Suspense
        fallback={
          <main className={s.page}>
            <DetailSkeleton />
          </main>
        }
      >
        <SessionsInner />
      </Suspense>
    </MotionRoot>
  );
}
