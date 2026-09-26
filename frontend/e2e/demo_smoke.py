"""Browser smoke test for /demo failure paths (no backend, no microphone needed).

Mocks the backend HTTP API and WebSocket with Playwright routing, emulates Chrome 14x's
Promise-returning scrollIntoView (the production crash trigger) and a denied microphone,
then checks the page stays usable.

  cd frontend && npm run build && npx next start -p 3100 &
  python e2e/demo_smoke.py http://localhost:3100      # needs `pip install playwright`
"""

from __future__ import annotations

import asyncio
import json
import os
import struct
import sys

from playwright.async_api import async_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3100"
API = os.environ.get("SMOKE_API_BASE", "http://localhost:8000")

CAPS = {
    "version": "smoke", "disclaimer": "synthetic", "providers": {"llm": "cloudflare:x", "stt": "cartesia:ink-whisper",
    "tts": "cartesia:sonic-3", "telephony": "fake"}, "mock_mode": {}, "browser_voice_available": True, "real_tts": True,
    "response_mode": "template", "telephony": {"enabled_flag": False, "configured": False, "active": False,
    "operator_endpoints": False, "allowed_numbers_configured": 0, "transfer_number_configured": False},
    "languages": ["en", "ja"], "latency_budget_ms": 1500, "policy": {}, "transcript_retention_days": 30,
    "raw_audio_persisted": False,
}  # fmt: skip
SCEN = [{"key": "A", "title": "Cooperative payer", "title_ja": "t", "summary": "s", "debtor_name": "Haruto Sato",
         "debtor_name_ja": "佐藤 陽翔", "synthetic_date_of_birth": "1988-04-12", "outstanding_balance": 80000,
         "currency": "JPY", "allowed_min_payment": 20000, "max_extension_days": 30,
         "script_en": ["Yes, this is Haruto speaking."], "script_ja": [], "tags": []}]  # fmt: skip

INIT = """
Element.prototype.scrollIntoView = function () { return Promise.resolve(); };  // Chrome 14x behaviour
navigator.mediaDevices.getUserMedia = () => Promise.reject(Object.assign(new Error('denied'), {name: 'NotAllowedError'}));
"""


async def main() -> int:
    failures: list[str] = []
    received: list[str] = []
    async with async_playwright() as p:
        exe = os.environ.get("CHROMIUM_PATH") or None
        b = await p.chromium.launch(executable_path=exe)
        page = await b.new_page()
        page_errors: list[str] = []
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        await page.add_init_script(INIT)
        await page.route(f"{API}/api/capabilities", lambda r: r.fulfill(json=CAPS))
        await page.route(f"{API}/api/scenarios", lambda r: r.fulfill(json=SCEN))
        sockets = []

        def on_ws(ws):
            sockets.append(ws)
            ws.on_message(lambda m: received.append(m if isinstance(m, str) else f"<{len(m)} bytes>"))
            ws.send(json.dumps({"type": "session.created", "session_id": f"s{len(sockets)}", "input_mode": "voice",
                                "providers": CAPS["providers"]}))  # fmt: skip
            ws.send(json.dumps({"type": "lifecycle", "from": "IDLE", "to": "AGENT_SPEAKING", "cause": "x", "at": 1}))
            for i in range(3):
                ws.send(json.dumps({"type": "agent.speaking", "index": i, "text": f"Hello {i}", "acts": "GREETING"}))
            ws.send("{not json")                                    # malformed JSON
            ws.send(json.dumps([1, 2, 3]))                          # unexpected shape
            ws.send(b"\x00\x01")                                    # frame shorter than header
            ws.send(struct.pack(">I", 1) + b"\x01\x00\x02")         # odd-length PCM

        await page.route_web_socket("**/ws/session**", on_ws)
        await page.goto(f"{BASE}/demo")
        start = page.get_by_role("button", name="Start voice session")
        await start.wait_for()
        await start.click()
        try:  # double activation: must not open a second session
            await start.click(timeout=500)
        except Exception:
            pass
        await page.wait_for_timeout(1500)
        body = await page.inner_text("body")
        if ("couldn" in body and "load" in body) or "Application error" in body:
            failures.append("generic error page shown")
        if "Microphone unavailable. Continue with typed input." not in body:
            failures.append("microphone notice missing")
        if "Hello 2" not in body:
            failures.append("transcript not rendered")
        if len(sockets) != 1:
            failures.append(f"expected one session, got {len(sockets)}")
        try:
            await page.get_by_placeholder("Speak, or type").fill("yes this is me", timeout=3000)
            await page.get_by_role("button", name="Send").click(timeout=3000)
            await page.wait_for_timeout(300)
        except Exception as e:
            failures.append(f"typed input unusable: {type(e).__name__}")
        if not any("yes this is me" in m for m in received):
            failures.append("typed fallback did not reach the socket")
        if page_errors:
            failures.append(f"uncaught page errors: {page_errors}")
        await b.close()
    print("SMOKE", "FAIL" if failures else "PASS", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
