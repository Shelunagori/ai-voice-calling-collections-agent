# Demo script

## 90-second flow (recommended)

1. **(0:00)** Landing page: read the one-line principle and the provider pills (they say `mock` honestly
   when no keys are set). Click **Start browser voice demo**.
2. **(0:10)** Choose **日本語**, scenario **A**, **Start voice session**. Point at *Agent state*
   (`AGENT_SPEAKING`) and the policy feed: the greeting already has `IDENTITY_REQUIRED_BEFORE_DISCLOSURE: BLOCK`.
3. **(0:20)** Click 「はい、本人です。」 then 「1988年4月12日です。」. Identity → `VERIFIED`; balance appears only now.
4. **(0:35)** While the agent is still reading the balance, click 「2週間後に3万円払えます。」 — barge-in: the
   bubble is struck through, lifecycle shows `INTERRUPTED`, the barge-in pill shows the cancel time.
5. **(0:45)** Agent reads back ¥30,000 + date. Click 「はい、それでお願いします。」 → promise `CONFIRMED`, SMS
   confirmation in the mock outbox.
6. **(0:55)** **New session**, English, scenario **C**, verify, then "Can I pay 15,000 yen in 60 days?" →
   `PAYMENT_DATE_WITHIN_MAX_EXTENSION: BLOCK`; "Yes, I agree" does not create a promise.
7. **(1:15)** End → **Inspect audit trail**: every decision with reason and timestamp; download JSON.
8. **(1:25)** **Evaluation → Run evaluation suite**: 32/32 invariants-based cases.

## Scenario reference (synthetic)

| Key | Debtor | Balance | Envelope | What it shows |
|---|---|---|---|---|
| A | Haruto Sato / 佐藤 陽翔, DOB 1988-04-12 | ¥80,000 | min ¥20,000, ≤ 30 days | cooperative promise (¥30,000) |
| B | Yui Tanaka / 田中 結衣, 1992-11-03 | ¥150,000 | min ¥30,000, ≤ 21 days | cannot pay in full → negotiation inside limits |
| C | Kenji Watanabe / 渡辺 健二, 1979-06-21 | ¥60,000 | min ¥15,000, ≤ 14 days | 60-day request blocked |
| D | Aiko Suzuki / 鈴木 愛子, 1985-02-14 | ¥45,000 | — | wrong party, no disclosure |
| E | Daiki Ito / 伊藤 大輝, 1990-08-30 | ¥70,000 | — | stop-contact |
| F | Mei Kobayashi / 小林 芽依, 1995-01-09 | ¥50,000 | min ¥10,000 | interruption |
| G | Ren Yamamoto / 山本 蓮, 1983-12-01 | ¥90,000 | — | human transfer |

Things worth trying: correct the amount during the read-back ("actually 25,000"); ask for a discount;
say "stop calling me" before verifying; type "um" (thinking/unclear handling); say "yes" while the read-back
is still playing (it is repeated rather than accepted).

## With credentials

- `STT_PROVIDER=cartesia` + `TTS_PROVIDER=cartesia` (+ voice ids): the microphone button is enabled; speak
  instead of clicking. Latency panel then shows real provider timings.
- `LLM_PROVIDER=cloudflare`: free-form phrasing is understood by the LLM; caller turn metadata shows
  `llm_used`.
- Twilio: **Telephony** (`/telephony`) → Start Call to your allowlisted number → **Open Session**.
