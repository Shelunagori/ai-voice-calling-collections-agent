"""Record the README demo video: browser voice demo (JA, scenario A, typed input on the
deployed backend) followed by the audit trail of a real PSTN session.

Run on a machine that can reach the deployed site (not in CI):

  cd frontend/e2e
  pip3 install playwright && python3 -m playwright install chromium
  python3 record_demo.py                      # → out/demo.webm + out/steps.json
  python3 record_demo.py --pstn babb58a4      # audit panel of that real-call session (id prefix)

Headed Chromium is used on purpose: the page needs an AudioContext to play real TTS.
The GIF is produced afterwards with ffmpeg (see out/README-GIF.md written by this script).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PWTimeout, sync_playwright

DEFAULT_BASE = "https://ai-voice-calling-collections-agent.vercel.app"
DEFAULT_API = "https://ai-voice-calling-collections-agent-production.up.railway.app"
OUT = Path(__file__).resolve().parent / "out"
VIEWPORT = {"width": 1280, "height": 800}

JA = {
    "confirm": "はい、本人です。",
    "dob": "1988年4月12日です。",
    "offer": "2週間後に3万円払えます。",
    "accept": "はい、それでお願いします。",
}


class Steps:
    def __init__(self) -> None:
        self.t0 = time.monotonic()
        self.rows: list[dict[str, object]] = []

    def mark(self, name: str, caption: str) -> None:
        t = round(time.monotonic() - self.t0, 2)
        self.rows.append({"t": t, "step": name, "caption": caption})
        print(f"[{t:7.2f}s] {name}: {caption}", flush=True)


def interrupt_btn(page: Page):
    return page.get_by_role("button", name="Interrupt agent")


def wait_agent_speaking(page: Page, timeout: int = 30_000) -> None:
    page.wait_for_function(
        "() => { const b=[...document.querySelectorAll('button')].find(x=>x.textContent.trim()==='Interrupt agent'); return b && !b.disabled; }",
        timeout=timeout,
    )


def wait_agent_done(page: Page, timeout: int = 60_000) -> None:
    # Agent finished (LISTENING): the interrupt button is disabled again. Wait for speaking first so we
    # never accept before the read-back has been played (policy D4: read-back must be heard).
    page.wait_for_function(
        "() => { const b=[...document.querySelectorAll('button')].find(x=>x.textContent.trim()==='Interrupt agent'); return b && b.disabled; }",
        timeout=timeout,
    )


def finished_count(page: Page) -> int:
    # Agent bubbles in the Conversation panel only ever grow (the lifecycle panel shows the last 6 rows only).
    return int(page.evaluate("() => document.querySelectorAll('.msg.agent').length"))


def wait_turn_complete(page: Page, before: int, timeout: int = 60_000) -> None:
    """Wait until a new agent utterance exists, is not interrupted, and the runtime is back in LISTENING —
    i.e. the agent has fully played it (a repeated read-back after a barge-in is its own utterance, so the
    next chip is never sent over an unfinished read-back)."""
    page.wait_for_function(
        """n => {
          const msgs = document.querySelectorAll('.msg.agent');
          if (msgs.length <= n) return false;
          const last = msgs[msgs.length - 1];
          if (last.classList.contains('interrupted')) return false;
          const on = document.querySelector('.st.on');
          return !!on && on.textContent.trim() === 'LISTENING';
        }""",
        arg=before,
        timeout=timeout,
    )


def chip(page: Page, text: str):
    return page.locator("button.chip", has_text=text)


def say(page: Page, text: str, steps: Steps, name: str, caption: str, settle: float = 0.6) -> None:
    before = finished_count(page)
    chip(page, text).click()
    steps.mark(name, caption)
    wait_turn_complete(page, before)
    time.sleep(settle)


def find_session_id(page: Page, api: str, prefix: str) -> str | None:
    """Resolve the PSTN session to show: the requested prefix if the server still has it, otherwise the
    best available real phone call (promise confirmed > stop-contact / transfer > any verified call)."""
    try:
        rows = page.request.get(f"{api}/api/sessions?limit=100").json()
    except Exception as e:  # noqa: BLE001
        print(f"WARN: could not list sessions from {api}: {e}", file=sys.stderr)
        return None
    rows = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    for r in rows:
        if str(r.get("id", "")).startswith(prefix):
            return str(r["id"])
    phone = [r for r in rows if r.get("channel") == "phone"]

    def rank(r: dict) -> int:
        if r.get("promise_status") == "CONFIRMED":
            return 0
        if r.get("ended_reason") in ("stop_contact_requested", "transferred_to_human"):
            return 1
        if r.get("identity_status") == "VERIFIED":
            return 2
        return 3

    phone.sort(key=rank)
    if phone:
        r = phone[0]
        print(f"note: {prefix} not on the server; showing phone session {str(r['id'])[:8]} "
              f"(promise={r.get('promise_status')}, ended={r.get('ended_reason')})", file=sys.stderr)
        return str(r["id"])
    return None


def slow_scroll(page: Page, px: int, step: int = 120, pause: float = 0.12) -> None:
    done = 0
    while done < px:
        page.mouse.wheel(0, step)
        done += step
        time.sleep(pause)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--api", default=DEFAULT_API, help="backend base URL, used only to resolve --pstn")
    ap.add_argument("--pstn", default="babb58a4", help="session-id prefix of the real PSTN call to show")
    ap.add_argument("--pstn-id", default=None, help="full session id of the real PSTN call (skips the API lookup)")
    ap.add_argument("--headless", action="store_true")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    steps = Steps()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=args.headless,
            args=["--autoplay-policy=no-user-gesture-required", "--mute-audio"],
        )
        ctx = browser.new_context(
            viewport=VIEWPORT,
            record_video_dir=str(OUT),
            record_video_size=VIEWPORT,
            locale="ja-JP",
            color_scheme="light",
        )
        page = ctx.new_page()
        steps.t0 = time.monotonic()  # video starts with the context

        # 0. Landing
        page.goto(f"{args.base}/", wait_until="networkidle")
        steps.mark("landing", "AI voice collections agent — real PSTN calls, deterministic policy, full audit")
        time.sleep(3.0)

        # 1. Demo setup: JA, typed input, scenario A
        page.goto(f"{args.base}/demo", wait_until="networkidle")
        page.get_by_role("button", name="日本語").click()
        page.get_by_role("button", name="Typed input").click()
        page.locator("label", has_text="協力的なお客様").locator("input[type=radio]").check()
        steps.mark("setup", "Japanese · scenario A · typed input (same runtime; mic needs headphones)")
        time.sleep(2.5)
        page.get_by_role("button", name="Start voice session").click()

        # 2. Greeting: identity required, nothing disclosed
        wait_agent_speaking(page)
        steps.mark("greeting", "Greeting: IDENTITY_REQUIRED_BEFORE_DISCLOSURE → BLOCK (no balance yet)")
        wait_agent_done(page)
        time.sleep(0.8)

        # 3. Identity
        say(page, JA["confirm"], steps, "confirm_name", "Caller confirms name")
        before = finished_count(page)
        chip(page, JA["dob"]).click()
        steps.mark("dob", "Date of birth matches → identity VERIFIED → balance disclosed only now")

        # 4. Barge-in while the agent reads the balance (real Cartesia TTS is playing)
        wait_agent_speaking(page)
        time.sleep(1.8)
        before = finished_count(page)
        chip(page, JA["offer"]).click()
        steps.mark("barge_in", "Barge-in mid-sentence: TTS cancelled, generation invalidated (~1 ms)")
        wait_turn_complete(page, before)  # the read-back has now been fully played
        steps.mark("readback", "Policy checks ALLOW (min, ≤ balance, ≤ 30 days) → read-back of ¥30,000 + date")
        time.sleep(1.0)

        # 5. Explicit yes only after the read-back was heard (D4). If the runtime repeats the read-back
        #    (yes arrived too early), wait for it and answer once more.
        for attempt in range(3):
            say(page, JA["accept"], steps, "accept", "Explicit yes after the read-back was played → promise CONFIRMED", settle=0.3)
            if page.locator("text=/\\bCONFIRMED\\b/").count() > 0:
                break
            print(f"note: promise not confirmed yet (attempt {attempt + 1}); read-back repeated", file=sys.stderr)
        else:
            print("WARN: CONFIRMED not seen on page", file=sys.stderr)
        time.sleep(3.0)

        # 6. End → audit trail of this browser session
        try:
            page.get_by_role("button", name="End session").click(timeout=5_000)
        except PWTimeout:
            pass  # the agent already ended the call
        link = page.get_by_role("link", name="Inspect audit trail")
        link.wait_for(timeout=15_000)
        time.sleep(1.5)
        link.click()
        page.wait_for_selector("[data-testid=session-detail]", timeout=30_000)
        steps.mark("audit_browser", "Audit trail: every policy decision with reason and timestamp")
        time.sleep(2.0)
        slow_scroll(page, 900)
        time.sleep(1.5)

        # 7. Real PSTN call audit panel
        page.goto(f"{args.base}/sessions", wait_until="networkidle")
        time.sleep(1.5)
        full_id = args.pstn_id or find_session_id(page, args.api, args.pstn)
        if full_id:
            page.goto(f"{args.base}/sessions?id={full_id}")
        else:
            print(f"WARN: session {args.pstn} not found via API; opening the first phone session", file=sys.stderr)
            page.locator("tr", has_text="phone").first.locator("a").click()
        page.wait_for_selector("[data-testid=session-detail], [data-testid=detail-error]", timeout=30_000)
        if page.locator("[data-testid=detail-error]").count():
            print(f"ERROR: session {full_id} → {page.locator('[data-testid=detail-error]').inner_text()}", file=sys.stderr)
            return 2
        steps.mark("audit_pstn", "Real PSTN call (Twilio + Cartesia + Cloudflare): identity → partial DOB → promise")
        time.sleep(2.5)
        slow_scroll(page, 700)
        steps.mark("audit_pstn_bargein", "3 barge-ins on the phone call, cancel path 1.2 ms · per-stage latency")
        slow_scroll(page, 900)
        time.sleep(3.0)
        steps.mark("end", "github.com/Shelunagori/ai-voice-calling-collections-agent")
        time.sleep(2.0)

        video = page.video
        ctx.close()
        path = Path(video.path()) if video else None
        browser.close()

    if path and path.exists():
        final = OUT / "demo.webm"
        path.replace(final)
        print(f"video: {final}")
    (OUT / "steps.json").write_text(json.dumps(steps.rows, ensure_ascii=False, indent=2))
    print(f"steps: {OUT / 'steps.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
