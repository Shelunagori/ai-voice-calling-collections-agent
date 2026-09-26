"""Contact points: a dialled/calling phone number at which a real person answered.

A stop-contact request heard on a call is recorded for the debtor *and* for the contact
point, so no scenario/account may call that person again. The number itself is never
persisted: only a salted SHA-256 key and a masked label for the operator view.
"""

from __future__ import annotations

import hashlib

_SALT = "vca-contact-point:v1:"


def contact_key(e164: str) -> str:
    return hashlib.sha256(f"{_SALT}{e164.strip()}".encode()).hexdigest()


def mask_number(e164: str) -> str:
    v = e164.strip()
    return f"{v[:3]}{'•' * max(len(v) - 6, 1)}{v[-3:]}" if len(v) > 6 else "•••"
