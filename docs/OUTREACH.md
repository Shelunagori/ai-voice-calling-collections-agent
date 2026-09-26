# Outreach templates

Replace the bracketed placeholders before sending. Links:

- Live demo: https://ai-voice-calling-collections-agent.vercel.app
- GitHub: https://github.com/Shelunagori/ai-voice-calling-collections-agent

## Email (hiring manager / recruiter)

**Subject:** Built a real-time AI voice collections POC for [Company] – [Role]

Hi [Name],

I'm applying for the [Role] position and built a small production-style POC in the same problem space.

It places real PSTN calls through Twilio Media Streams, streams speech through Cartesia (STT and TTS), and
uses an LLM only to interpret what the caller said. Identity, repayment rules, stop-contact, transfer state
and every audit decision stay deterministic in the application layer.

What I focused on:

- interruption / barge-in with measured cancellation
- identity-before-disclosure
- repayment-negotiation guardrails and explicit promise-to-pay confirmation
- stop-contact suppression across the debtor and the dialled number
- session audit trail and per-stage latency instrumentation
- real failure cases found during PSTN testing, for example a partial date of birth that an unconstrained
  fallback parser read as a payment amount

On real calls it has completed identity verification through to a confirmed promise-to-pay, recorded a
stop-contact request, ended a failed verification without disclosing anything, and recorded a
human-transfer request (the transfer itself is simulated in the demo).

It's a synthetic-data POC with simulated policy rules, and latency isn't consistently under 1.5 s yet.
The README is upfront about both.

Live demo: https://ai-voice-calling-collections-agent.vercel.app
GitHub: https://github.com/Shelunagori/ai-voice-calling-collections-agent

I'd be glad to walk through the architecture and the trade-offs.

Best,
Shailendra Nagori

## Short recruiter DM

Hi [Name], I'm applying for [Role] at [Company]. I built a real-time AI voice collections POC: live Twilio
PSTN calls, Cartesia STT/TTS, and LLM intent understanding, with identity, payment rules, stop-contact and
audit kept deterministic in code. Demo: https://ai-voice-calling-collections-agent.vercel.app · Code:
https://github.com/Shelunagori/ai-voice-calling-collections-agent. Happy to walk through it.

## LinkedIn message

Hi [Name], I saw the [Role] opening at [Company]. To go deeper on the problem space I built a voice
collections agent POC.

- It makes real PSTN calls (Twilio Media Streams + Cartesia) and uses an LLM only for understanding.
- A deterministic controller owns identity-before-disclosure, repayment limits, promise confirmation,
  stop-contact and transfer.
- Every decision and latency stage is in the session audit.

Testing on real calls surfaced two bugs I then fixed: partial dates of birth and stop-contact scope.

Demo: https://ai-voice-calling-collections-agent.vercel.app
Code: https://github.com/Shelunagori/ai-voice-calling-collections-agent

Would you be open to a short chat?

## Application form: project summary (< 500 characters)

Real-time AI voice collections POC: live Twilio PSTN calls, Cartesia streaming STT/TTS and Cloudflare LLM
intent parsing. A deterministic controller enforces identity-before-disclosure, repayment limits, explicit
promise-to-pay, stop-contact and transfer. Also: barge-in, EN/JA, audit trail, per-turn latency and a
32-scenario eval suite. Synthetic data, simulated policy.
