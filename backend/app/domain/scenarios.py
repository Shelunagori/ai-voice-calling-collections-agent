"""Deterministic synthetic demo scenarios. All identities and accounts are fictional.

UUIDs are derived with uuid5 so seed data is stable across machines and restarts.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date

from .models import AccountTerms, DebtorProfile, Language

_NS = uuid.UUID("7d1b6c1e-5c2a-4f0e-9a7e-000000c0ffee")
ORG_NAME = {Language.EN: "Sakura Demo Services", Language.JA: "さくらデモサービス"}


def _id(kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(_NS, f"{kind}:{key}")


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    title_ja: str
    summary: str
    debtor: DebtorProfile
    account: AccountTerms
    # Suggested caller lines shown in the demo UI and replayed by the evaluation suite.
    script_en: tuple[str, ...] = ()
    script_ja: tuple[str, ...] = ()
    tags: tuple[str, ...] = field(default_factory=tuple)

    def script(self, lang: Language) -> tuple[str, ...]:
        return self.script_ja if lang == Language.JA else self.script_en

    def to_public(self) -> dict[str, object]:
        """What the browser may see. The DOB is shown because these are synthetic
        identities and the reviewer needs it to pass verification."""
        return {
            "key": self.key,
            "title": self.title,
            "title_ja": self.title_ja,
            "summary": self.summary,
            "debtor_name": self.debtor.full_name,
            "debtor_name_ja": self.debtor.display_name_ja,
            "synthetic_date_of_birth": self.debtor.date_of_birth.isoformat(),
            "outstanding_balance": self.account.outstanding_balance,
            "currency": self.account.currency,
            "allowed_min_payment": self.account.allowed_min_payment,
            "max_extension_days": self.account.max_extension_days,
            "script_en": list(self.script_en),
            "script_ja": list(self.script_ja),
            "tags": list(self.tags),
        }


def _debtor(key: str, name: str, ja: str, aliases: tuple[str, ...], dob: date, phone: str) -> DebtorProfile:
    return DebtorProfile(
        debtor_id=_id("debtor", key),
        full_name=name,
        name_aliases=aliases,
        date_of_birth=dob,
        phone_e164=phone,
        preferred_language=Language.JA,
        display_name_ja=ja,
    )


def _account(key: str, debtor: DebtorProfile, balance: int, minimum: int, days: int, **kw: object) -> AccountTerms:
    return AccountTerms(
        account_id=_id("account", key),
        debtor_id=debtor.debtor_id,
        creditor_name=ORG_NAME[Language.EN],
        outstanding_balance=balance,
        currency="JPY",
        allowed_min_payment=minimum,
        max_extension_days=days,
        **kw,  # type: ignore[arg-type]
    )


# Synthetic phone numbers use the +81-90-0000-xxxx range and are never dialled
# unless explicitly added to DEMO_CALL_ALLOWED_NUMBERS by the operator.
_A = _debtor(
    "A",
    "Haruto Sato",
    "佐藤 陽翔",
    ("haruto sato", "sato haruto", "佐藤陽翔", "佐藤"),
    date(1988, 4, 12),
    "+819000000001",
)
_B = _debtor(
    "B", "Yui Tanaka", "田中 結衣", ("yui tanaka", "tanaka yui", "田中結衣", "田中"), date(1992, 11, 3), "+819000000002"
)
_C = _debtor(
    "C", "Kenji Watanabe", "渡辺 健二", ("kenji watanabe", "渡辺健二", "渡辺"), date(1979, 6, 21), "+819000000003"
)
_D = _debtor("D", "Aiko Suzuki", "鈴木 愛子", ("aiko suzuki", "鈴木愛子", "鈴木"), date(1985, 2, 14), "+819000000004")
_E = _debtor("E", "Daiki Ito", "伊藤 大輝", ("daiki ito", "伊藤大輝", "伊藤"), date(1990, 8, 30), "+819000000005")
_F = _debtor(
    "F", "Mei Kobayashi", "小林 芽依", ("mei kobayashi", "小林芽依", "小林"), date(1995, 1, 9), "+819000000006"
)
_G = _debtor("G", "Ren Yamamoto", "山本 蓮", ("ren yamamoto", "山本蓮", "山本"), date(1983, 12, 1), "+819000000007")

SCENARIOS: dict[str, Scenario] = {
    s.key: s
    for s in [
        Scenario(
            "A",
            "Cooperative payer",
            "協力的なお客様",
            "Verified caller agrees to ¥30,000 on a date inside the 30-day extension window.",
            _A,
            _account("A", _A, 80_000, 20_000, 30),
            (
                "Yes, this is Haruto speaking.",
                "April 12, 1988.",
                "I can pay 30,000 yen in two weeks.",
                "Yes, that's correct.",
                "No, that's all. Thank you.",
            ),
            (
                "はい、本人です。",
                "1988年4月12日です。",
                "2週間後に3万円払えます。",
                "はい、それでお願いします。",
                "いいえ、大丈夫です。",
            ),
            ("happy_path", "promise_to_pay"),
        ),
        Scenario(
            "B",
            "Cannot pay in full",
            "全額の支払いが難しい",
            "Caller cannot pay ¥150,000; agent negotiates only inside the approved minimum and window.",
            _B,
            _account("B", _B, 150_000, 30_000, 21),
            (
                "Yes, speaking.",
                "November 3rd, 1992.",
                "I can't pay the full amount right now.",
                "I could do 30,000 yen next Friday.",
                "Yes.",
            ),
            (
                "はい、そうです。",
                "1992年11月3日です。",
                "今は全額は払えません。",
                "来週の金曜日に3万円なら払えます。",
                "はい。",
            ),
            ("partial_payment", "negotiation"),
        ),
        Scenario(
            "C",
            "Extension beyond policy",
            "規定を超える延長の要望",
            "Caller asks for 60 days; policy allows 14. The proposal must not be confirmed.",
            _C,
            _account("C", _C, 60_000, 15_000, 14),
            (
                "Yes, this is Kenji.",
                "June 21, 1979.",
                "Can I pay 15,000 yen in 60 days?",
                "Yes, I agree.",
                "Okay, 15,000 yen next Friday then.",
                "Yes.",
            ),
            (
                "はい、本人です。",
                "1979年6月21日です。",
                "60日後に1万5千円でもいいですか。",
                "はい、お願いします。",
                "では来週の金曜日に1万5千円で。",
                "はい。",
            ),
            ("policy_violation_attempt", "invalid_extension"),
        ),
        Scenario(
            "D",
            "Wrong party",
            "ご本人以外",
            "The person answering is not the account holder. No account details may be disclosed.",
            _D,
            _account("D", _D, 45_000, 10_000, 30),
            ("Who is this? How much does Aiko owe?", "No, I'm her husband."),
            ("どちら様ですか。いくら借りているんですか。", "いいえ、夫です。"),
            ("wrong_identity", "no_disclosure"),
        ),
        Scenario(
            "E",
            "Stop contact",
            "連絡停止の要望",
            "Verified caller asks not to be contacted again. Future contact is deterministically disabled.",
            _E,
            _account("E", _E, 70_000, 20_000, 30),
            ("Yes, this is Daiki.", "August 30, 1990.", "Please stop calling me. Don't contact me again."),
            ("はい、伊藤です。", "1990年8月30日です。", "もう電話しないでください。"),
            ("stop_contact",),
        ),
        Scenario(
            "F",
            "Interruption (barge-in)",
            "割り込み（バージイン）",
            "Caller talks over the agent while it is speaking; playback is cancelled and the new turn is handled.",
            _F,
            _account("F", _F, 50_000, 10_000, 30),
            (
                "Yes, this is Mei.",
                "January 9, 1995.",
                "[interrupt] Sorry, I can pay 10,000 yen tomorrow.",
                "Yes, correct.",
            ),
            (
                "はい、小林です。",
                "1995年1月9日です。",
                "[interrupt] すみません、明日1万円払えます。",
                "はい、そうです。",
            ),
            ("interruption", "barge_in"),
        ),
        Scenario(
            "G",
            "Human transfer",
            "担当者への転送",
            "Caller asks for a human. The session enters a deterministic transfer state.",
            _G,
            _account("G", _G, 90_000, 20_000, 30),
            ("Yes, this is Ren.", "I want to speak to a real person."),
            ("はい、山本です。", "担当者と話したいです。"),
            ("human_transfer",),
        ),
    ]
}


def get_scenario(key: str) -> Scenario:
    try:
        return SCENARIOS[key.upper()]
    except KeyError as e:
        raise KeyError(f"unknown scenario {key!r}") from e
