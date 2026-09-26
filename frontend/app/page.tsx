import Link from "next/link";
import { StatusStrip } from "@/components/StatusStrip";

const CAPS = [
  ["Streaming voice", "Mic → streaming STT → end-of-turn → policy → LLM/templates → streaming TTS → speaker."],
  ["Barge-in", "Caller speech cancels agent audio mid-sentence; stale audio is dropped by generation id."],
  ["Policy guardrails", "Identity-before-disclosure, payment envelope, stop-contact, calling hours — deterministic and audited."],
  ["Promise-to-pay", "Only confirmed after an explicit “yes” to a read-back that was actually heard."],
  ["Japanese / English", "Explicit language selection; bilingual prompts, templates, number and date parsing."],
  ["PSTN adapter", "Twilio Voice + Media Streams behind a TelephonyProvider; disabled unless configured."],
  ["Evaluation harness", "30 regression scenarios with deterministic invariants; optional LLM-as-judge."],
  ["Audit trail", "Every policy decision, state change, barge-in and provider failure is reconstructable."],
];

export default function Home() {
  return (
    <main className="wrap narrow">
      <section className="hero stack">
        <h1>AI Voice Collections Agent</h1>
        <p className="lead">Real-time collection conversations with deterministic policy controls.</p>
        <div className="row">
          <Link className="btn primary" href="/demo">
            Start browser voice demo
          </Link>
          <Link className="btn" href="/architecture">
            Explore architecture
          </Link>
        </div>
        <StatusStrip />
      </section>

      <div className="banner" style={{ marginBottom: 18 }}>
        Portfolio proof-of-concept. All identities and accounts are synthetic. Policy rules are simulated demo rules
        inspired by regulated collections workflows — not legal requirements, not certified, and not suitable for
        contacting real debtors.
      </div>

      <section className="grid cols-4" style={{ marginBottom: 18 }}>
        {CAPS.map(([t, d]) => (
          <div key={t} className="panel cap">
            <b>{t}</b>
            <span>{d}</span>
          </div>
        ))}
      </section>

      <section className="grid cols-2">
        <div className="panel">
          <h2>Core design principle</h2>
          <p>
            <b>Language models handle language. Application code owns authority.</b>
          </p>
          <p className="muted">
            The model may interpret the caller and propose typed actions. It cannot verify identity, disclose the
            balance, approve a payment plan, set stop-contact, confirm a promise or perform a transfer — those are
            deterministic state transitions in the conversation controller, each gated by the policy engine and written
            to the audit trail.
          </p>
        </div>
        <div className="panel">
          <h2>Two-minute reviewer walkthrough</h2>
          <ol className="small" style={{ paddingLeft: 18, margin: 0 }}>
            <li>Open the voice demo, pick English or 日本語 and scenario A.</li>
            <li>Verify identity with the synthetic date of birth shown on the card.</li>
            <li>Offer ¥30,000 in two weeks, then confirm — watch the promise-to-pay panel.</li>
            <li>Try scenario C (60-day extension) and watch the policy engine block it.</li>
            <li>Interrupt the agent mid-sentence (scenario F) and read the barge-in latency.</li>
            <li>End the session and open its audit trail; then run the evaluation suite.</li>
          </ol>
        </div>
      </section>
    </main>
  );
}
