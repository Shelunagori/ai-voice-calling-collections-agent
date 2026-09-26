"""Response planning, bilingual templates and the output disclosure guard.

The controller decides *what* may be said (a `ResponsePlan` with an explicit set of
approved facts). Templates or an LLM decide *how* it is phrased. Whatever the
phrasing source, `guard()` checks the final text before it is spoken:

* before identity verification, no amounts or debt vocabulary may be spoken;
* after verification, every amount/date spoken must be one the plan approved;
* threats and unauthorised concessions (waive, sue, 差し押さえ ...) are never allowed.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

from .models import Language


class Act(StrEnum):
    GREETING = "GREETING"
    ASK_NAME_AGAIN = "ASK_NAME_AGAIN"
    ASK_DOB = "ASK_DOB"
    DOB_RETRY = "DOB_RETRY"
    ASK_DOB_PART = "ASK_DOB_PART"  # ask only for the missing year / month / day
    DOB_INVALID = "DOB_INVALID"
    IDENTITY_FAILED = "IDENTITY_FAILED"
    WRONG_PARTY = "WRONG_PARTY"
    PRE_VERIFICATION = "PRE_VERIFICATION"
    PURPOSE = "PURPOSE"
    DISCLOSE = "DISCLOSE"
    BALANCE_INFO = "BALANCE_INFO"
    ASK_AMOUNT = "ASK_AMOUNT"
    ASK_DATE = "ASK_DATE"
    ASK_PLAN = "ASK_PLAN"
    CONFIRM_PROPOSAL = "CONFIRM_PROPOSAL"
    REJECT_PROPOSAL = "REJECT_PROPOSAL"
    OFFER_TERMS = "OFFER_TERMS"
    NO_DISCOUNT = "NO_DISCOUNT"
    PTP_CONFIRMED = "PTP_CONFIRMED"
    PROPOSAL_DECLINED = "PROPOSAL_DECLINED"
    ALREADY_CONFIRMED = "ALREADY_CONFIRMED"
    TRANSFER = "TRANSFER"
    DISPUTE_TRANSFER = "DISPUTE_TRANSFER"
    STOP_CONTACT_ACK = "STOP_CONTACT_ACK"
    CLARIFY = "CLARIFY"
    REPROMPT_SILENCE = "REPROMPT_SILENCE"
    SILENCE_END = "SILENCE_END"
    CLOSING = "CLOSING"


# ----------------------------------------------------------------------------------
# formatting
# ----------------------------------------------------------------------------------


def fmt_money(amount: int, currency: str, lang: Language) -> str:
    if currency != "JPY":
        return f"{amount:,} {currency}"
    return f"{amount:,}円" if lang == Language.JA else f"¥{amount:,}"


def fmt_date(d: date, lang: Language) -> str:
    if lang == Language.JA:
        wd = "月火水木金土日"[d.weekday()]
        return f"{d.year}年{d.month}月{d.day}日（{wd}）"
    return f"{d:%A}, {d:%B} {d.day}, {d.year}"


# ----------------------------------------------------------------------------------
# templates
# ----------------------------------------------------------------------------------

T: dict[Act, dict[Language, str]] = {
    Act.GREETING: {
        Language.EN: "Hello, this is Aoi, an automated assistant calling from {org}. This call may be recorded. "
        "Am I speaking with {name}?",
        Language.JA: "もしもし。こちらは{org}の自動音声アシスタント、アオイです。この通話は録音されることがあります。"
        "{name}様でいらっしゃいますか。",
    },
    Act.ASK_NAME_AGAIN: {
        Language.EN: "Am I speaking with {name}?",
        Language.JA: "恐れ入りますが、{name}様でいらっしゃいますか。",
    },
    Act.ASK_DOB: {
        Language.EN: "Thank you. To protect your privacy, could you please tell me your date of birth?",
        Language.JA: "ありがとうございます。ご本人確認のため、生年月日をお教えいただけますか。",
    },
    Act.DOB_RETRY: {
        Language.EN: "I'm sorry, that doesn't match our records. Could you repeat your date of birth, please?",
        Language.JA: "申し訳ございません、記録と一致しませんでした。もう一度、生年月日をお願いできますか。",
    },
    # Rendered by the controller from the known/missing parts; never repeats digits
    # (the pre-verification guard forbids numbers and the caller's DOB is not echoed).
    Act.ASK_DOB_PART: {Language.EN: "{dob_question}", Language.JA: "{dob_question}"},
    Act.DOB_INVALID: {
        Language.EN: "I'm sorry, that doesn't seem to be a valid date. Could you tell me your date of birth again, "
        "please?",
        Language.JA: "申し訳ございません、日付が正しくないようです。もう一度、生年月日をお願いできますか。",
    },
    Act.IDENTITY_FAILED: {
        Language.EN: "I'm sorry, I wasn't able to verify your identity, so I can't discuss this matter. "
        "Please call the number on your letter. Goodbye.",
        Language.JA: "申し訳ございませんが、ご本人確認ができなかったため、この件についてはお話しできません。"
        "お手元の書面に記載の番号までお問い合わせください。失礼いたします。",
    },
    Act.WRONG_PARTY: {
        Language.EN: "Thank you, and sorry to have bothered you. Could you let {first_name} know that {org} called? "
        "Goodbye.",
        Language.JA: "承知いたしました。お手数をおかけしました。{org}から連絡があった旨、{family_name}様にお伝え"
        "いただけますでしょうか。失礼いたします。",
    },
    Act.PRE_VERIFICATION: {
        Language.EN: "I can share the details once I've confirmed your identity.",
        Language.JA: "詳細はご本人確認の後にお伝えいたします。",
    },
    Act.PURPOSE: {
        Language.EN: "I'm calling about a personal business matter for {name}, so I need to confirm your identity first.",
        Language.JA: "{name}様への個人的なご用件でお電話いたしました。まずご本人確認をさせていただきます。",
    },
    Act.DISCLOSE: {
        Language.EN: "Thank you, you're verified. I'm calling about your account, which has an outstanding balance of "
        "{balance}. Today I can arrange a payment of at least {min} on or before {latest}. "
        "How much could you pay, and when?",
        Language.JA: "ご本人確認ができました。お客様のアカウントには{balance}の未払い残高がございます。本日は{min}以上の"
        "お支払いを、{latest}までの日付でお約束いただけます。いつ、おいくらお支払いいただけますか。",
    },
    Act.BALANCE_INFO: {
        Language.EN: "Your outstanding balance is {balance}. The minimum payment I can arrange is {min}, on or before "
        "{latest}.",
        Language.JA: "未払い残高は{balance}です。お約束いただける最低金額は{min}で、{latest}までの日付となります。",
    },
    Act.ASK_AMOUNT: {
        Language.EN: "How much could you pay on {date}? The minimum is {min}.",
        Language.JA: "{date}に、おいくらお支払いいただけますか。最低金額は{min}です。",
    },
    Act.ASK_DATE: {
        Language.EN: "On what date could you pay {amount}? It needs to be on or before {latest}.",
        Language.JA: "{amount}のお支払いは、いつになりますか。{latest}までの日付でお願いいたします。",
    },
    Act.ASK_PLAN: {
        Language.EN: "How much could you pay, and on what date?",
        Language.JA: "いつ、おいくらお支払いいただけますか。",
    },
    Act.CONFIRM_PROPOSAL: {
        Language.EN: "Just to confirm: you'll pay {amount} on {date}. Is that correct?",
        Language.JA: "確認いたします。{date}に{amount}をお支払いいただく、ということでよろしいでしょうか。",
    },
    Act.REJECT_PROPOSAL: {
        Language.EN: "{reasons} Would a payment of at least {min} on or before {latest} work for you?",
        Language.JA: "{reasons}{latest}までに{min}以上のお支払いは可能でしょうか。",
    },
    Act.OFFER_TERMS: {
        Language.EN: "I understand. I can arrange a smaller payment: at least {min}, on any date up to {latest}. "
        "Would that be possible?",
        Language.JA: "承知いたしました。{min}以上であれば、{latest}までのご都合のよい日にお支払いいただけます。"
        "いかがでしょうか。",
    },
    Act.NO_DISCOUNT: {
        Language.EN: "I'm sorry, I'm not able to change the balance. I can arrange a payment of at least {min} "
        "by {latest}, or transfer you to a member of our team. Which would you prefer?",
        Language.JA: "申し訳ございませんが、残高の変更はいたしかねます。{latest}までに{min}以上のお支払いをお約束いただくか、"
        "担当者におつなぎすることができます。どちらがよろしいですか。",
    },
    Act.PTP_CONFIRMED: {
        Language.EN: "Thank you. Your promise to pay {amount} on {date} is recorded, and we'll send you a confirmation "
        "message. Is there anything else I can help with?",
        Language.JA: "ありがとうございます。{date}に{amount}のお支払いのお約束を承りました。確認のメッセージをお送りします。"
        "ほかにご用件はございますか。",
    },
    Act.PROPOSAL_DECLINED: {
        Language.EN: "No problem. What amount and date would work for you?",
        Language.JA: "承知いたしました。ご都合のよい金額と日付をお教えください。",
    },
    Act.ALREADY_CONFIRMED: {
        Language.EN: "Your promise to pay {amount} on {date} is already recorded. For any change, I can transfer you "
        "to a member of our team.",
        Language.JA: "{date}に{amount}のお支払いのお約束はすでに承っております。変更をご希望の場合は担当者におつなぎします。",
    },
    Act.TRANSFER: {
        Language.EN: "Of course. I'm transferring you to a member of our team now. Please hold.",
        Language.JA: "かしこまりました。ただいま担当者におつなぎします。少々お待ちください。",
    },
    Act.DISPUTE_TRANSFER: {
        Language.EN: "I understand you're disputing this. I've noted that, and I'll transfer you to a member of our team "
        "who can help. Please hold.",
        Language.JA: "ご納得いただけない点があるとのこと、承知いたしました。記録いたしまして、担当者におつなぎします。"
        "少々お待ちください。",
    },
    Act.STOP_CONTACT_ACK: {
        Language.EN: "Understood. I've recorded your request, and we won't call you again about this matter. Goodbye.",
        Language.JA: "承知いたしました。ご要望を記録しましたので、この件で今後お電話することはございません。失礼いたします。",
    },
    Act.CLARIFY: {
        Language.EN: "Sorry, I didn't quite catch that.",
        Language.JA: "申し訳ございません、うまく聞き取れませんでした。",
    },
    Act.REPROMPT_SILENCE: {
        Language.EN: "Hello, are you still there?",
        Language.JA: "もしもし、聞こえていらっしゃいますか。",
    },
    Act.SILENCE_END: {
        Language.EN: "I haven't heard anything, so I'll end the call now. Goodbye.",
        Language.JA: "お声が確認できませんので、お電話を終了いたします。失礼いたします。",
    },
    Act.CLOSING: {
        Language.EN: "Thank you for your time. Goodbye.",
        Language.JA: "お時間をいただきありがとうございました。失礼いたします。",
    },
}

REJECT_REASON: dict[str, dict[Language, str]] = {
    "PAYMENT_MIN_AMOUNT": {
        Language.EN: "I'm not able to accept less than {min}.",
        Language.JA: "申し訳ございませんが、{min}未満のお支払いはお受けできません。",
    },
    "PAYMENT_NOT_ABOVE_BALANCE": {
        Language.EN: "That's more than the outstanding balance of {balance}.",
        Language.JA: "その金額は未払い残高の{balance}を超えています。",
    },
    "PAYMENT_DATE_WITHIN_MAX_EXTENSION": {
        Language.EN: "I can only arrange a date up to {latest}, which is {max_days} days from today.",
        Language.JA: "お支払日は{latest}まで、本日から{max_days}日以内でお願いしております。",
    },
}


@dataclass
class ResponsePlan:
    acts: list[Act]
    language: Language
    slots: dict[str, str] = field(default_factory=dict)
    disclosure_allowed: bool = False
    approved_amounts: set[int] = field(default_factory=set)
    approved_dates: set[date] = field(default_factory=set)
    end_call: bool = False
    transfer: bool = False

    def render(self) -> str:
        parts = []
        for act in self.acts:
            tmpl = T[act][self.language]
            parts.append(tmpl.format_map(_Slots(self.slots)))
        sep = "" if self.language == Language.JA else " "
        return sep.join(p.strip() for p in parts if p.strip())

    @property
    def primary(self) -> Act:
        return self.acts[-1] if self.acts else Act.CLARIFY


class _Slots(dict[str, str]):
    def __missing__(self, key: str) -> str:
        raise KeyError(f"response slot '{key}' not provided")


# ----------------------------------------------------------------------------------
# guard
# ----------------------------------------------------------------------------------

_DEBT_WORDS_EN = re.compile(
    r"\b(debts?|owe[sd]?|owing|balance|overdue|arrears|payments?|pay|repay|collections?|loans?|outstanding|"
    r"bills?|unpaid|due|past due|yen|jpy|dollars?|installments?|thousand|hundred|million)\b",
    re.I,
)
_DEBT_WORDS_JA = [
    "借金",
    "残高",
    "滞納",
    "返済",
    "支払",
    "延滞",
    "未払",
    "債務",
    "督促",
    "ローン",
    "円",
    "請求",
    "万",
    "千",
]
_FORBIDDEN_EN = re.compile(
    r"\b(waive|waived|forgive|forgiven|discount|sue|lawsuit|court|arrest|police|garnish|seize|jail|prison|"
    r"guarantee[d]? approval|legal action)\b",
    re.I,
)
_FORBIDDEN_JA = ["免除", "減額", "値引", "訴訟", "裁判", "差し押さえ", "差押", "逮捕", "警察", "刑務所", "法的措置"]


@dataclass
class GuardResult:
    ok: bool
    violations: list[str]


def _amounts_in(text: str) -> list[int]:
    """Every money amount a listener could hear: ¥N, N円, N yen, JPY N, spoken numbers
    ("fifty thousand yen", 三万円). Dates are masked first so years are not amounts."""
    from . import nlu_rules

    t = nlu_rules.normalise(text)
    today = date(2026, 1, 1)  # only used to resolve relative expressions while masking
    en_dates = nlu_rules._find_dates_en(t, today)
    ja_dates = nlu_rules._find_dates_ja(t, today)
    masked = nlu_rules._mask(t, en_dates.spans + ja_dates.spans)
    vals = nlu_rules._find_amounts_en(masked) + nlu_rules._find_amounts_ja(masked)
    for m in re.finditer(r"\bjpy\s*([\d,]+)", masked):
        vals.append(int(m[1].replace(",", "")))
    return vals


def guard(text: str, plan: ResponsePlan, rendered_template: str | None = None) -> GuardResult:
    t = unicodedata.normalize("NFKC", text)
    v: list[str] = []
    if _FORBIDDEN_EN.search(t) or any(w in t for w in _FORBIDDEN_JA):
        v.append("forbidden_concession_or_threat")
    if not plan.disclosure_allowed:
        if _DEBT_WORDS_EN.search(t) or any(w in t for w in _DEBT_WORDS_JA):
            v.append("debt_vocabulary_before_verification")
        if re.search(r"¥|\d{3,}", t):
            v.append("number_before_verification")
    else:
        for amt in _amounts_in(t):
            if amt not in plan.approved_amounts:
                v.append(f"unapproved_amount:{amt}")
        approved_md = {(d.month, d.day) for d in plan.approved_dates}
        for md in _month_days_in(t):
            if md not in approved_md:
                v.append(f"unapproved_date:{md[0]}-{md[1]}")
    return GuardResult(ok=not v, violations=v)


_EN_MONTHS = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
]
_EN_MD = re.compile(r"\b(" + "|".join(_EN_MONTHS) + r")\s+(\d{1,2})\b", re.I)
_EN_DM = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(" + "|".join(_EN_MONTHS) + r")\b", re.I)
_NUM_MD = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})(?:/\d{2,4})?(?![\d/])")
_ISO = re.compile(r"\b\d{4}-(\d{2})-(\d{2})\b")
_JA_MD = re.compile(r"(\d{1,2})月(\d{1,2})日")


def _month_days_in(text: str) -> list[tuple[int, int]]:
    out = [(_EN_MONTHS.index(m[1].lower()) + 1, int(m[2])) for m in _EN_MD.finditer(text)]
    out += [(_EN_MONTHS.index(m[2].lower()) + 1, int(m[1])) for m in _EN_DM.finditer(text)]
    out += [(int(m[1]), int(m[2])) for m in _NUM_MD.finditer(text)]
    out += [(int(m[1]), int(m[2])) for m in _ISO.finditer(text)]
    out += [(int(m[1]), int(m[2])) for m in _JA_MD.finditer(text)]
    return out


_NUMBER_WORDS = re.compile(
    r"\b(zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|"
    r"sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|"
    r"thousand|million)\b",
    re.I,
)
_KANJI_NUM = re.compile("[〇一二三四五六七八九十百千万億]")


def numbers_match_template(candidate: str, template: str) -> bool:
    """A rephrasing may not introduce any number the approved template did not contain."""
    c = unicodedata.normalize("NFKC", candidate).replace(",", "")
    t = unicodedata.normalize("NFKC", template).replace(",", "")
    if not set(re.findall(r"\d+", c)) <= set(re.findall(r"\d+", t)):
        return False
    if {w.lower() for w in _NUMBER_WORDS.findall(c)} - {w.lower() for w in _NUMBER_WORDS.findall(t)}:
        return False
    return set(_KANJI_NUM.findall(c)) <= set(_KANJI_NUM.findall(t))
