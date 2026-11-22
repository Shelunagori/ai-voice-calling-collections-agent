"use client";

import { m } from "framer-motion";
import Link from "next/link";
import { useRef } from "react";
import { VoiceOrb } from "@/components/orb/VoiceOrb";
import type { OrbSignal } from "@/components/orb/types";
import { BackendStatus } from "@/components/ui/BackendStatus";
import { CtaLink, MicIcon } from "@/components/ui/Cta";
import { cx } from "@/components/ui/cx";
import { MotionRoot, SPRING } from "@/components/ui/motion";
import ui from "@/components/ui/ui.module.css";
import l from "./landing.module.css";

const POINTS = [
  {
    n: "01",
    tone: "caller",
    title: "Talks like a person",
    body: "Streaming STT → end-of-turn → policy → templates or LLM → streaming TTS. The caller can cut in mid-sentence; stale agent audio is dropped by generation id.",
    href: "/demo",
    link: "Try the voice demo",
  },
  {
    n: "02",
    tone: "agent",
    title: "Answers to policy",
    body: "Identity before disclosure, payment envelope, stop-contact and calling hours are deterministic checks. A promise only counts after an explicit “yes” to a read-back that was actually heard.",
    href: "/architecture",
    link: "See how authority is split",
  },
  {
    n: "03",
    tone: "ok",
    title: "Proves it",
    body: "32 regression scenarios with deterministic invariants, and an audit trail that reconstructs every policy decision, state change, barge-in and provider failure.",
    href: "/evaluation",
    link: "Open the evaluation",
  },
] as const;

const CAPS = [
  ["Streaming voice", "Mic → streaming STT → end-of-turn → policy → LLM/templates → streaming TTS → speaker."],
  ["Barge-in", "Caller speech cancels agent audio mid-sentence; stale audio is dropped by generation id."],
  ["Policy guardrails", "Identity-before-disclosure, payment envelope, stop-contact, calling hours — deterministic and audited."],
  ["Promise-to-pay", "Only confirmed after an explicit “yes” to a read-back that was actually heard."],
  ["Japanese / English", "Explicit language selection; bilingual prompts, templates, number and date parsing."],
  ["PSTN adapter", "Twilio Voice + Media Streams behind a TelephonyProvider; disabled unless configured."],
  ["Evaluation harness", "32 regression scenarios with deterministic invariants; optional LLM-as-judge."],
  ["Audit trail", "Every policy decision, state change, barge-in and provider failure is reconstructable."],
];

const WALK: [string, string | null][] = [
  ["Open the voice demo, pick English or 日本語 and scenario A.", "/demo"],
  ["Verify identity with the synthetic date of birth shown on the card.", null],
  ["Offer ¥30,000 in two weeks, then confirm — watch the promise-to-pay panel.", null],
  ["Try scenario C (60-day extension) and watch the policy engine block it.", null],
  ["Interrupt the agent mid-sentence (scenario F) and read the barge-in latency.", null],
  ["End the session and open its audit trail; then run the evaluation suite.", "/sessions"],
];

const reveal = {
  initial: { opacity: 0, y: 24, filter: "blur(6px)" },
  whileInView: { opacity: 1, y: 0, filter: "blur(0px)" },
  viewport: { once: true, margin: "-60px" },
} as const;

export function Landing() {
  // Decorative only: the landing orb has no call behind it. It breathes, and leans in when the CTA is hovered.
  const hover = useRef(false);
  const signal = useRef<() => OrbSignal>(() => (hover.current ? { state: "LISTENING", amp: 0.25 } : { state: "IDLE", amp: 0 }));

  return (
    <MotionRoot>
      <main className={l.page}>
        <section className={l.hero}>
          <m.div className={l.orb} initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }} transition={{ duration: 1, ease: [0.22, 1, 0.36, 1] }}>
            <VoiceOrb signal={signal} />
          </m.div>
          <m.div
            className={l.heroCopy}
            initial={{ opacity: 0, y: 24, filter: "blur(8px)" }}
            animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
            transition={{ duration: 0.7, delay: 0.1, ease: [0.22, 1, 0.36, 1] }}
          >
            <span className={ui.overline}>AI voice collections agent · inspired by production work</span>
            <h1 className={ui.h1}>
              Talks like a person.
              <br />
              <span className={ui.grad}>Answers to policy.</span>
            </h1>
            <p className={ui.lead}>Real-time collection conversations with deterministic policy controls.</p>
            <div className={l.ctaRow}>
              <CtaLink
                href="/demo"
                onPointerEnter={() => {
                  hover.current = true;
                }}
                onPointerLeave={() => {
                  hover.current = false;
                }}
              >
                <MicIcon />
                Start a voice call
              </CtaLink>
              <Link className={cx(ui.ghostBtn, l.ghostBig)} href="/architecture">
                Explore architecture
              </Link>
            </div>
            <p className={l.heroNote}>Live backend · synthetic debtors · typed input works without a microphone</p>
            <BackendStatus />
          </m.div>
        </section>

        <section className={l.section} aria-labelledby="pts">
          <h2 id="pts" className={l.srOnly}>
            What it does
          </h2>
          <div className={l.points}>
            {POINTS.map((p, i) => (
              <m.article key={p.n} className={l.point} data-tone={p.tone} {...reveal} transition={{ ...SPRING, delay: i * 0.08 }}>
                <span className={l.pointN}>{p.n}</span>
                <h3 className={l.pointTitle}>{p.title}</h3>
                <p>{p.body}</p>
                <Link href={p.href} className={l.pointLink}>
                  {p.link} <span aria-hidden="true">→</span>
                </Link>
              </m.article>
            ))}
          </div>
        </section>

        <div className={cx(l.section, l.disclaimer)}>
          <div className={ui.banner}>
            <span>
              Portfolio proof-of-concept. All identities and accounts are synthetic. Policy rules are simulated demo rules inspired by regulated collections workflows — not
              legal requirements, not certified, and not suitable for contacting real debtors.
            </span>
          </div>
        </div>

        <m.section className={l.principle} {...reveal} transition={SPRING}>
          <span className={ui.overline}>Core design principle</span>
          <p className={l.quote}>
            Language models handle language.
            <br />
            <span className={ui.grad}>Application code owns authority.</span>
          </p>
          <p className={ui.lead}>
            The model may interpret the caller and propose typed actions. It cannot verify identity, disclose the balance, approve a payment plan, set stop-contact,
            confirm a promise or perform a transfer — those are deterministic state transitions in the conversation controller, each gated by the policy engine and
            written to the audit trail.
          </p>
        </m.section>

        <section className={l.section} aria-labelledby="caps">
          <m.div className={l.sectionHead} {...reveal} transition={SPRING}>
            <span className={ui.overline}>Under the hood</span>
            <h2 id="caps" className={ui.h2}>
              Eight pieces, <span className={ui.grad}>one runtime.</span>
            </h2>
          </m.div>
          <div className={l.caps}>
            {CAPS.map(([t, body], i) => (
              <m.div key={t} className={l.cap} {...reveal} transition={{ duration: 0.5, delay: (i % 4) * 0.05, ease: [0.22, 1, 0.36, 1] }}>
                <b>{t}</b>
                <span>{body}</span>
              </m.div>
            ))}
          </div>
        </section>

        <section className={cx(l.section, l.walkWrap)} aria-labelledby="walk">
          <m.div className={l.sectionHead} {...reveal} transition={SPRING}>
            <span className={ui.overline}>For reviewers</span>
            <h2 id="walk" className={ui.h2}>
              The two-minute <span className={ui.grad}>walkthrough.</span>
            </h2>
          </m.div>
          <ol className={l.walk}>
            {WALK.map(([step, href], i) => (
              <m.li key={i} {...reveal} transition={{ duration: 0.5, delay: i * 0.05, ease: [0.22, 1, 0.36, 1] }}>
                <span className={l.walkN}>{String(i + 1).padStart(2, "0")}</span>
                <span className={l.walkText}>{step}</span>
                {href && (
                  <Link href={href} className={l.walkGo} aria-label={`Go: ${step}`}>
                    →
                  </Link>
                )}
              </m.li>
            ))}
          </ol>
          <div className={l.walkCta}>
            <CtaLink href="/demo">
              <MicIcon />
              Start a voice call
            </CtaLink>
          </div>
        </section>

      </main>
    </MotionRoot>
  );
}
