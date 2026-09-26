"""Twilio Voice adapter (REST via httpx; no SDK).

* place_call: Calls.json with a TwiML webhook URL and status callbacks.
* transfer:   update the live call with TwiML <Dial> to the human queue number.
* hangup:     update the call with Status=completed.
Webhook authenticity is checked with X-Twilio-Signature (see telephony.signature).
"""

from __future__ import annotations

import logging
from xml.sax.saxutils import escape

import httpx

from ..observability import metrics
from ..telephony.signature import validate_twilio_signature
from .base import CallHandle, ErrorKind, ProviderError, TransferResult

log = logging.getLogger(__name__)
API = "https://api.twilio.com/2010-04-01/Accounts/{sid}"


class TwilioTelephony:
    name = "twilio"
    live = True

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
        webhook_base_url: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not (account_sid and auth_token and from_number and webhook_base_url):
            raise ValueError("Twilio configuration incomplete")
        self.sid = account_sid
        self._token = auth_token
        self.from_number = from_number
        self.base = webhook_base_url.rstrip("/")
        self._api = API.format(sid=account_sid)
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=3.0), auth=(account_sid, auth_token)
        )

    async def _post(self, path: str, data: dict[str, str | list[str]]) -> dict[str, object]:
        try:
            r = await self._client.post(f"{self._api}{path}", data=data)
        except httpx.HTTPError as e:
            metrics.inc("provider_errors", {"provider": "twilio", "kind": "unavailable"})
            raise ProviderError("twilio", ErrorKind.UNAVAILABLE, type(e).__name__) from e
        if r.status_code >= 300:
            metrics.inc("provider_errors", {"provider": "twilio", "kind": str(r.status_code)})
            detail = ""
            try:
                detail = str(r.json().get("message", ""))[:200]
            except ValueError:
                pass
            kind = ErrorKind.AUTH if r.status_code in (401, 403) else ErrorKind.BAD_REQUEST
            raise ProviderError("twilio", kind, f"HTTP {r.status_code} {detail}")
        return r.json()

    async def place_call(self, to_e164: str, session_id: str) -> CallHandle:
        data: dict[str, str | list[str]] = {
            "To": to_e164,
            "From": self.from_number,
            "Url": f"{self.base}/telephony/twilio/voice?session_id={session_id}",
            "Method": "POST",
            "StatusCallback": f"{self.base}/telephony/twilio/status",
            "StatusCallbackMethod": "POST",
            "StatusCallbackEvent": ["initiated", "ringing", "answered", "completed"],
            "Timeout": "25",
        }
        res = await self._post("/Calls.json", data)
        return CallHandle(self.name, str(res.get("sid")), str(res.get("status")))

    async def transfer(self, call_id: str, to_e164: str) -> TransferResult:
        twiml = f"<Response><Dial>{escape(to_e164)}</Dial></Response>"
        try:
            await self._post(f"/Calls/{call_id}.json", {"Twiml": twiml})
        except ProviderError as e:
            return TransferResult(ok=False, status="failed", detail=str(e))
        return TransferResult(ok=True, status="dialing", detail="call redirected to <Dial>")

    async def hangup(self, call_id: str) -> None:
        await self._post(f"/Calls/{call_id}.json", {"Status": "completed"})

    def validate_signature(self, url: str, params: dict[str, str], signature: str) -> bool:
        return validate_twilio_signature(self._token, url, params, signature)

    async def aclose(self) -> None:
        await self._client.aclose()
