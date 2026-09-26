"""Twilio request signature validation (X-Twilio-Signature).

Algorithm (per Twilio docs): take the full URL, append each POST parameter name and
value sorted by name, HMAC-SHA1 with the account auth token, base64-encode.
Implemented without the Twilio SDK so domain code has no SDK dependency.
"""

from __future__ import annotations

import base64
import hashlib
import hmac


def compute_twilio_signature(auth_token: str, url: str, params: dict[str, str] | None = None) -> str:
    payload = url + "".join(f"{k}{v}" for k, v in sorted((params or {}).items()))
    digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def validate_twilio_signature(auth_token: str, url: str, params: dict[str, str], signature: str) -> bool:
    if not auth_token or not signature:
        return False
    expected = compute_twilio_signature(auth_token, url, params)
    if hmac.compare_digest(expected, signature):
        return True
    # Twilio may sign with or without the default port; accept both forms.
    for a, b in ((":443/", "/"), (":80/", "/")):
        if a in url:
            alt = url.replace(a, b, 1)
            if hmac.compare_digest(compute_twilio_signature(auth_token, alt, params), signature):
                return True
    return False
