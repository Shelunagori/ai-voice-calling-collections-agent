"""Deterministic English/Japanese interpreter.

Used in three places:
1. as the whole language-understanding layer in mock mode (CI, local, evaluation);
2. as the fallback when the hosted LLM times out or returns invalid JSON;
3. as a *safety net* that runs alongside the LLM for caller-rights intents
   (stop-contact, human transfer). If either layer detects them they are honoured,
   so an LLM misclassification cannot silently drop a stop-contact request.

It is intentionally conservative: when unsure it returns UNCLEAR and the
controller asks a clarifying question instead of guessing an amount or date.
"""

from __future__ import annotations

import calendar
import re
import unicodedata
from datetime import date, timedelta

from .commands import Action, Interpretation, ProposedAction
from .models import DialogPhase, DobParts, Language
from .turn_context import TurnContext, constrain, context_for

# ----------------------------------------------------------------------------------
# keyword tables
# ----------------------------------------------------------------------------------

_EN = {
    Action.STOP_CONTACT: [
        r"\bstop (calling|contacting|phoning)\b",
        r"\bdo ?n[o']?t (ever )?(call|contact|phone)\b",
        r"\bdo not (ever )?(call|contact|phone)\b",
        r"\bnever (call|contact)\b",
        r"\bremove (me|my number)\b",
        r"\bno (more )?(calls|contact)\b",
        r"\bleave me alone\b",
        r"\bcease (all )?contact\b",
    ],
    Action.REQUEST_HUMAN: [
        r"\b(real|live|actual) (person|human)\b",
        r"\bhuman\b",
        r"\b(representative|operator|supervisor|manager)\b",
        r"\b(speak|talk) (to|with) (someone|somebody|a person|an agent|a person)\b",
        r"\btransfer me\b",
    ],
    Action.WRONG_PERSON: [
        r"\bwrong (number|person)\b",
        r"\b(it'?s|this is) not (me|him|her)\b",
        r"\bi'?m not (him|her|mr|ms|mrs)\b",
        r"\bi am not (him|her|mr|ms|mrs)\b",
        r"\b(this is|i'?m) (his|her) (brother|sister|wife|husband|mother|father|son|daughter|friend|colleague)\b",
        r"\b(he|she)('?s| is) not (here|home|available|in)\b",
        r"\bno one (by|with) that name\b",
        r"\bnever heard of\b",
    ],
    Action.DISPUTE: [
        r"\bnot my debt\b",
        r"\bdo ?n[o']?t owe\b",
        r"\bdispute\b",
        r"\bnever borrowed\b",
        r"\bthis is a scam\b",
    ],
    Action.CANNOT_PAY: [
        r"\bcan'?t (pay|afford)\b",
        r"\bcannot (pay|afford)\b",
        r"\bunable to pay\b",
        r"\bnot able to pay\b",
        r"\bdo ?n[o']?t have (the |enough )?(money|funds)\b",
        r"\blost my job\b",
        r"\bcan'?t do (the )?(full|whole|entire)\b",
        r"\bnot the (full|whole|entire) (amount|balance)\b",
    ],
    Action.REQUEST_DISCOUNT: [
        r"\bdiscount\b",
        r"\breduc(e|ed|tion)\b",
        r"\bwaive\b",
        r"\bforgive\b",
        r"\bsettle for less\b",
        r"\blower the (balance|amount)\b",
    ],
    Action.ASK_BALANCE: [r"\bhow much\b", r"\bbalance\b", r"\bwhat do i owe\b", r"\bamount due\b"],
    Action.ASK_PURPOSE: [
        r"\bwhat is this (about|regarding)\b",
        r"\bwho is (this|calling)\b",
        r"\bwhy are you calling\b",
        r"\bwhat'?s this about\b",
    ],
    Action.GOODBYE: [r"\bgood ?bye\b", r"\bbye\b", r"\bhang(ing)? up\b"],
}

_EN_AFFIRM = re.compile(
    r"^\s*(yes|yeah|yep|yup|correct|that'?s (right|correct)|right|speaking|sure|ok(ay)?|confirm(ed)?|"
    r"that works|sounds good|i agree|agreed|it is|that is me|this is (he|she|me)|i am|i'?m (him|her))\b"
)
_EN_DENY = re.compile(r"^\s*(no|nope|nah|not really|that'?s (wrong|not right|incorrect)|incorrect|wrong)\b")

_JA = {
    Action.STOP_CONTACT: [
        "電話しないで",
        "電話をしないで",
        "連絡しないで",
        "連絡をしないで",
        "かけてこないで",
        "かけないで",
        "連絡をやめて",
        "電話をやめて",
        "もう電話",
        "二度と電話",
        "二度と連絡",
        "連絡不要",
    ],
    Action.REQUEST_HUMAN: ["担当者", "人間", "オペレーター", "人と話", "上司", "責任者", "スタッフと"],
    Action.WRONG_PERSON: [
        "人違い",
        "番号違い",
        "間違い電話",
        "本人ではありません",
        "本人じゃない",
        "本人ではない",
        "家族です",
        "妻です",
        "(?<!丈)夫です",
        "兄です",
        "弟です",
        "姉です",
        "妹です",
        "母です",
        "父です",
        "留守",
        "おりません",
        "いません",
        "知りません",
    ],
    Action.DISPUTE: ["身に覚えがない", "借りていない", "借りてない", "払う必要はない", "詐欺"],
    Action.CANNOT_PAY: [
        "払えない",
        "支払えない",
        "払えません",
        "支払えません",
        "お金がない",
        "全額は",
        "無理です",
        "厳しい",
    ],
    Action.REQUEST_DISCOUNT: ["減額", "まけて", "安く", "免除", "値引"],
    Action.ASK_BALANCE: ["いくら", "残高", "金額は"],
    Action.ASK_PURPOSE: ["何の用", "どちら様", "何の件", "ご用件"],
    Action.GOODBYE: ["さようなら", "失礼します", "切ります"],
}
_JA_AFFIRM = [
    "はい",
    "ええ",
    "そうです",
    "本人です",
    "大丈夫です",
    "お願いします",
    "承知",
    "了解",
    "それで",
    "いいです",
    "結構です",
]
_JA_DENY = ["いいえ", "違います", "ちがいます", "いや、", "いえ、"]

_FILLERS = {
    "um",
    "uh",
    "hmm",
    "mm",
    "er",
    "ah",
    "oh",
    "えー",
    "えーと",
    "あの",
    "あのー",
    "うーん",
    "えっと",
}


def _has_japanese(t: str) -> bool:
    return bool(re.search("[\u3040-\u30ff\u4e00-\u9fff]", t))


def normalise(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).strip().lower()
    t = t.replace("’", "'")
    return re.sub(r"\s+", " ", t)


def is_filler_only(text: str) -> bool:
    words = [w for w in re.split(r"[\s、。,.!?！？]+", normalise(text)) if w]
    return not words or all(w in _FILLERS for w in words)


# ----------------------------------------------------------------------------------
# numbers
# ----------------------------------------------------------------------------------

_EN_UNITS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
    "a": 1,
}
_EN_SCALES = {"hundred": 100, "thousand": 1000, "million": 1_000_000}
_EN_NUMWORD = re.compile(
    r"\b((?:(?:" + "|".join(k for k in _EN_UNITS if k != "a") + r"|a|hundred|thousand|million|and)[\s-]+)*"
    r"(?:" + "|".join(k for k in _EN_UNITS if k != "a") + r"|hundred|thousand|million))\b"
)


def _words_to_int(phrase: str) -> int | None:
    total, current, seen = 0, 0, False
    for tok in re.split(r"[\s-]+", phrase):
        if tok == "and" or not tok:
            continue
        if tok in _EN_UNITS:
            current += _EN_UNITS[tok]
            seen = True
        elif tok == "hundred":
            current = max(current, 1) * 100
            seen = True
        elif tok in ("thousand", "million"):
            total += max(current, 1) * _EN_SCALES[tok]
            current = 0
            seen = True
        else:
            return None
    return total + current if seen else None


_KANJI_DIGITS = {"〇": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_KANJI_SMALL = {"十": 10, "百": 100, "千": 1000}
_KANJI_BIG = {"万": 10_000, "億": 100_000_000}


def _ja_number(s: str) -> int | None:
    """Parse '3万5千', '三万', '30,000', '2万5000'."""
    s = s.replace(",", "")
    if s.isdigit():
        return int(s)
    total, section, num = 0, 0, None
    i = 0
    while i < len(s):
        ch = s[i]
        if ch.isdigit():
            j = i
            while j < len(s) and s[j].isdigit():
                j += 1
            num = int(s[i:j])
            i = j
            continue
        if ch in _KANJI_DIGITS:
            num = _KANJI_DIGITS[ch]
        elif ch in _KANJI_SMALL:
            section += (num if num is not None else 1) * _KANJI_SMALL[ch]
            num = None
        elif ch in _KANJI_BIG:
            section += num or 0
            total += (section or 1) * _KANJI_BIG[ch]
            section, num = 0, None
        else:
            return None
        i += 1
    return total + section + (num or 0)


# ----------------------------------------------------------------------------------
# dates
# ----------------------------------------------------------------------------------

_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
_MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
_MONTHS["sept"] = 9
_WEEKDAYS = {d.lower(): i for i, d in enumerate(calendar.day_name)}
_WEEKDAYS.update({d.lower(): i for i, d in enumerate(calendar.day_abbr)})
_JA_WEEKDAYS = {"月": 0, "火": 1, "水": 2, "木": 3, "金": 4, "土": 5, "日": 6}
_MONTH_RE = "|".join(sorted(_MONTHS, key=len, reverse=True))


def add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _next_dom(today: date, day: int) -> date | None:
    for k in range(0, 3):
        base = add_months(today.replace(day=1), k)
        if day <= calendar.monthrange(base.year, base.month)[1]:
            cand = base.replace(day=day)
            if cand > today:
                return cand
    return None


def _month_day(today: date, month: int, day: int) -> date | None:
    try:
        cand = date(today.year, month, day)
    except ValueError:
        return None
    return cand if cand >= today else cand.replace(year=today.year + 1)


def _next_weekday(today: date, wd: int, force_next_week: bool = False) -> date:
    delta = (wd - today.weekday()) % 7
    if delta == 0:
        delta = 7
    if force_next_week and delta < 7:
        # "next Friday": the Friday of next week
        start_next_week = today + timedelta(days=7 - today.weekday())
        return start_next_week + timedelta(days=wd)
    return today + timedelta(days=delta)


_NUM_TOKEN = r"(\d+|" + "|".join(k for k in _EN_UNITS if k != "a") + r"|a|an)"


def _small_num(tok: str) -> int | None:
    if tok.isdigit():
        return int(tok)
    if tok in ("a", "an"):
        return 1
    return _EN_UNITS.get(tok)


class _Found:
    def __init__(self) -> None:
        self.dates: list[date] = []
        self.days: list[int] = []
        self.spans: list[tuple[int, int]] = []


def _find_dates_en(t: str, today: date) -> _Found:
    f = _Found()

    def take(m: re.Match[str], d: date | None = None, days: int | None = None) -> None:
        f.spans.append(m.span())
        if d is not None:
            f.dates.append(d)
        if days is not None:
            f.days.append(days)

    for m in re.finditer(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b", t):
        try:
            take(m, date(int(m[1]), int(m[2]), int(m[3])))
        except ValueError:
            pass
    for m in re.finditer(rf"\b({_MONTH_RE})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?\b", t):
        mo, dy = _MONTHS[m[1]], int(m[2])
        d = _safe_date(int(m[3]), mo, dy) if m[3] else _month_day(today, mo, dy)
        if d:
            take(m, d)
    for m in re.finditer(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH_RE})\.?(?:,?\s+(\d{{4}}))?\b", t):
        if _overlaps(m.span(), f.spans):
            continue
        mo, dy = _MONTHS[m[2]], int(m[1])
        d = _safe_date(int(m[3]), mo, dy) if m[3] else _month_day(today, mo, dy)
        if d:
            take(m, d)
    for m in re.finditer(r"\btomorrow\b", t):
        take(m, today + timedelta(days=1))
    for m in re.finditer(r"\btoday\b", t):
        take(m, today)
    for m in re.finditer(r"\bend of (the |this )?month\b", t):
        take(m, today.replace(day=calendar.monthrange(today.year, today.month)[1]))
    for m in re.finditer(r"\bnext month\b", t):
        take(m, add_months(today, 1))
    for m in re.finditer(rf"\b(?:in\s+)?{_NUM_TOKEN}\s+(day|week|month)s?\b(?:\s+from now)?", t):
        n = _small_num(m[1])
        if n is None:
            continue
        unit = m[2]
        if unit == "day":
            take(m, days=n)
        elif unit == "week":
            take(m, days=7 * n)
        else:
            take(m, add_months(today, n))
    for m in re.finditer(r"\b(next\s+)?(" + "|".join(_WEEKDAYS) + r")\b", t):
        take(m, _next_weekday(today, _WEEKDAYS[m[2]], bool(m[1])))
    for m in re.finditer(r"\b(?:on\s+)?the\s+(\d{1,2})(?:st|nd|rd|th)\b", t):
        if _overlaps(m.span(), f.spans):
            continue
        d = _next_dom(today, int(m[1]))
        if d:
            take(m, d)
    return f


_JA_NUM = r"([0-9〇零一二三四五六七八九十百千]+)"


def _find_dates_ja(t: str, today: date) -> _Found:
    f = _Found()

    def take(m: re.Match[str], d: date | None = None, days: int | None = None) -> None:
        f.spans.append(m.span())
        if d is not None:
            f.dates.append(d)
        if days is not None:
            f.days.append(days)

    era = {"昭和": 1925, "平成": 1988, "令和": 2018}
    for m in re.finditer(r"(昭和|平成|令和)" + _JA_NUM + "年" + _JA_NUM + "月" + _JA_NUM + "日", t):
        y, mo, dy = (_ja_number(m[2]), _ja_number(m[3]), _ja_number(m[4]))
        if y and mo and dy:
            d = _safe_date(era[m[1]] + y, mo, dy)
            if d:
                take(m, d)
    for m in re.finditer(r"(\d{4})年" + _JA_NUM + "月" + _JA_NUM + "日", t):
        if _overlaps(m.span(), f.spans):
            continue
        mo, dy = _ja_number(m[2]), _ja_number(m[3])
        if mo and dy:
            d = _safe_date(int(m[1]), mo, dy)
            if d:
                take(m, d)
    for m in re.finditer(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", t):
        d = _safe_date(int(m[1]), int(m[2]), int(m[3]))
        if d:
            take(m, d)
    for m in re.finditer(r"来月" + _JA_NUM + "日", t):
        dy = _ja_number(m[1])
        if dy:
            base = add_months(today.replace(day=1), 1)
            d = _safe_date(base.year, base.month, dy)
            if d:
                take(m, d)
    for m in re.finditer(_JA_NUM + "月" + _JA_NUM + "日", t):
        if _overlaps(m.span(), f.spans):
            continue
        mo, dy = _ja_number(m[1]), _ja_number(m[2])
        if mo and dy:
            d = _month_day(today, mo, dy)
            if d:
                take(m, d)
    for m in re.finditer(_JA_NUM + r"(日|週間|ヶ月|か月|カ月)(後|以内|待って|ほど|くらい)?", t):
        if _overlaps(m.span(), f.spans):
            continue
        n = _ja_number(m[1])
        if n is None:
            continue
        unit = m[2]
        if unit == "日":
            if m[3]:
                take(m, days=n)
            else:
                d = _next_dom(today, n) if 1 <= n <= 31 else None
                if d:
                    take(m, d)
        elif unit == "週間":
            take(m, days=7 * n)
        else:
            take(m, add_months(today, n))
    for m in re.finditer("明日", t):
        take(m, today + timedelta(days=1))
    for m in re.finditer("今日", t):
        take(m, today)
    for m in re.finditer("(今月末|月末)", t):
        take(m, today.replace(day=calendar.monthrange(today.year, today.month)[1]))
    for m in re.finditer(r"(来週の?)?([月火水木金土日])曜", t):
        take(m, _next_weekday(today, _JA_WEEKDAYS[m[2]], bool(m[1])))
    return f


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _overlaps(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    return any(span[0] < b and a < span[1] for a, b in spans)


def _mask(t: str, spans: list[tuple[int, int]]) -> str:
    chars = list(t)
    for a, b in spans:
        for i in range(a, b):
            chars[i] = " "
    return "".join(chars)


def _find_amounts_en(t: str) -> list[int]:
    out: list[int] = []
    for m in re.finditer(r"(¥|yen\s*)?\s*(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(k|thousand|man)?\s*(yen|円)?", t):
        raw, suffix = m[2], m[3]
        has_currency = bool(m[1] or m[4])
        val = float(raw.replace(",", ""))
        if suffix in ("k", "thousand"):
            val *= 1000
        elif suffix == "man":
            val *= 10_000
        if has_currency or suffix or val >= 1000:
            out.append(int(val))
    for m in _EN_NUMWORD.finditer(t):
        v = _words_to_int(m[1])
        tail = t[m.end() : m.end() + 5]
        if v is not None and (v >= 1000 or "yen" in tail):
            out.append(v)
    return out


def _find_amounts_ja(t: str) -> list[int]:
    out: list[int] = []
    for m in re.finditer(r"([0-9,〇零一二三四五六七八九十百千万億]+)\s*(円|えん)", t):
        v = _ja_number(m[1])
        if v is not None:
            out.append(v)
    if not out:
        for m in re.finditer(r"([0-9〇零一二三四五六七八九十百千]+万[0-9〇零一二三四五六七八九十百千]*)", t):
            v = _ja_number(m[1])
            if v is not None:
                out.append(v)
        for m in re.finditer(r"¥\s*(\d{1,3}(?:,\d{3})+|\d+)", t):
            out.append(int(m[1].replace(",", "")))
        for m in re.finditer(r"(?<![0-9])(\d{1,3}(?:,\d{3})+|\d{4,})(?![0-9年月日])", t):
            out.append(int(m[1].replace(",", "")))
    return out


# ----------------------------------------------------------------------------------
# main entry
# ----------------------------------------------------------------------------------


def interpret(
    text: str,
    language: Language,
    today: date,
    expecting_dob: bool = False,
    context: TurnContext | None = None,
) -> Interpretation:
    """Deterministic reading of one caller turn.

    With a `context` (the normal runtime path) extraction is phase-aware: while identity
    is being checked, numbers are only date-of-birth candidates (never money); once
    negotiating, no DOB is extracted. The result is then constrained to the phase's
    allowed actions. Without a context the legacy, unconstrained behaviour is kept.
    """
    if context is None and expecting_dob:
        context = context_for(DialogPhase.IDENTITY_DOB)
    t = normalise(text)
    if not t or is_filler_only(t):
        return Interpretation(actions=[ProposedAction(action=Action.UNCLEAR)], source="rules")
    identity = context is not None and context.identity_phase
    interp = _interpret(
        t,
        language,
        today,
        money=not identity,
        dob_mode="parts" if identity else ("off" if context is not None else "legacy"),
    )
    return constrain(interp, context) if context is not None else interp


def _interpret(t: str, language: Language, today: date, *, money: bool, dob_mode: str) -> Interpretation:
    actions: list[ProposedAction] = []
    is_ja = _has_japanese(t)  # script-based: callers code-switch between languages
    if money:
        found = _find_dates_ja(t, today) if is_ja else _find_dates_en(t, today)
        rest = _mask(t, found.spans)
        amounts = _find_amounts_ja(rest) if is_ja else _find_amounts_en(rest)
    else:
        found, amounts = _Found(), []

    def hit(action: Action) -> bool:
        if is_ja:
            return any(re.search(k, t) for k in _JA.get(action, []))
        return any(re.search(p, t) for p in _EN.get(action, []))

    # caller-rights intents first
    for act in (Action.STOP_CONTACT, Action.REQUEST_HUMAN):
        if hit(act):
            actions.append(ProposedAction(action=act))

    if hit(Action.WRONG_PERSON):
        actions.append(ProposedAction(action=Action.WRONG_PERSON))

    dobs: list[date] = []
    if dob_mode == "legacy":
        # a full date in the past with a year
        dobs = [d for d in found.dates if d.year < today.year - 15]
        if dobs and len(found.dates) == 1:
            actions.append(ProposedAction(action=Action.PROVIDE_DOB, dob=dobs[0]))
    elif dob_mode == "parts":
        dob = dob_action(parse_dob(t, language, today))
        if dob is not None:
            actions.append(dob)

    if money:
        future_dates = [d for d in found.dates if d >= today]
        amount = amounts[-1] if amounts else None  # last mentioned amount wins ("20k... no, 25k")
        pay_date = future_dates[-1] if future_dates else None
        days = found.days[-1] if found.days and pay_date is None else None
        if (amount is not None or pay_date is not None or days is not None) and not dobs:
            actions.append(
                ProposedAction(action=Action.PROPOSE_PAYMENT, amount=amount, date=pay_date, days_from_now=days)
            )

    for act in (Action.DISPUTE, Action.REQUEST_DISCOUNT, Action.CANNOT_PAY, Action.ASK_BALANCE, Action.ASK_PURPOSE):
        if hit(act) and act not in [a.action for a in actions]:
            actions.append(ProposedAction(action=act))

    if is_ja:
        affirm = any(t.startswith(k) or k in t[:12] for k in _JA_AFFIRM)
        deny = any(t.startswith(k) for k in _JA_DENY)
    else:
        affirm = bool(_EN_AFFIRM.search(t))
        deny = bool(_EN_DENY.search(t))
    if deny:
        actions.insert(0, ProposedAction(action=Action.DENY))
    elif affirm:
        actions.insert(0, ProposedAction(action=Action.AFFIRM))

    if hit(Action.GOODBYE) and not actions:
        actions.append(ProposedAction(action=Action.GOODBYE))

    if not actions:
        actions.append(ProposedAction(action=Action.UNCLEAR))
    # de-duplicate, keep order, cap at 3 with priority to caller-rights intents
    seen: set[Action] = set()
    uniq = []
    for a in actions:
        if a.action not in seen:
            seen.add(a.action)
            uniq.append(a)
    priority = {Action.STOP_CONTACT: 0, Action.REQUEST_HUMAN: 1, Action.WRONG_PERSON: 2}
    uniq.sort(key=lambda a: priority.get(a.action, 5))
    return Interpretation(actions=uniq[:3], source="rules")


# ----------------------------------------------------------------------------------
# date of birth (complete or partial) — only ever what the caller said
# ----------------------------------------------------------------------------------


def dob_action(parts: DobParts) -> ProposedAction | None:
    """PROVIDE_DOB for a complete real date, otherwise PARTIAL_DOB with only the given parts."""
    d = parts.as_date()
    if d is not None:
        return ProposedAction(action=Action.PROVIDE_DOB, dob=d)
    if parts.has_any:
        return ProposedAction(action=Action.PARTIAL_DOB, dob_year=parts.year, dob_month=parts.month, dob_day=parts.day)
    return None


def dob_parts_of(a: ProposedAction) -> DobParts:
    if a.dob is not None:
        return DobParts(a.dob.year, a.dob.month, a.dob.day)
    return DobParts(a.dob_year, a.dob_month, a.dob_day)


_ORDINAL_WORDS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
    "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
    "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18, "nineteenth": 19, "twentieth": 20,
    "thirtieth": 30,
}  # fmt: skip
for _i, _w in enumerate(["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth"], 1):
    _ORDINAL_WORDS[f"twenty-{_w}"] = 20 + _i
    _ORDINAL_WORDS[f"twenty {_w}"] = 20 + _i
_ORDINAL_WORDS["thirty-first"] = _ORDINAL_WORDS["thirty first"] = 31
_ORD_RE = "|".join(sorted((re.escape(k) for k in _ORDINAL_WORDS), key=len, reverse=True))
_DAY_TOKEN = rf"(?:(\d{{1,2}})(?:st|nd|rd|th)?(?!\d)|({_ORD_RE}))"
# Abbreviations and "may" are only months next to a number ("may I ask" is not May).
_AMBIGUOUS_MONTHS = {"may", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec"}
_TEENS = "ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen"
_TENS = "twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety"
_ONES = "one|two|three|four|five|six|seven|eight|nine"
_SPELLED_YEAR = re.compile(
    rf"\b(nineteen|twenty)[\s-]+((?:{_TEENS})|(?:{_TENS})(?:[\s-]+(?:{_ONES}))?|oh[\s-]+(?:{_ONES}))\b"
    rf"|\btwo thousand(?:[\s-]+and)?(?:[\s-]+((?:{_TEENS})|(?:{_ONES})))?\b"
)


def _day_value(num: str | None, word: str | None) -> int | None:
    v = int(num) if num else _ORDINAL_WORDS.get(word or "")
    return v if v is not None and 1 <= v <= 31 else None


def _valid_year(y: int, today: date) -> int | None:
    return y if 1900 <= y <= today.year else None


def _dob_en(t: str, today: date) -> DobParts:
    m = re.search(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b", t)
    if m:
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
        if _valid_year(y, today) and 1 <= mo <= 12 and 1 <= d <= 31:
            return DobParts(y, mo, d)

    year: int | None = None
    for ym in re.finditer(r"(?<![\d,])(1[89]\d{2}|20\d{2})(?![\d,])", t):
        year = _valid_year(int(ym[1]), today) or year
    if year is None:
        for sm in _SPELLED_YEAR.finditer(t):
            if sm[1]:
                v = _words_to_int(sm[2].replace("oh", "").strip(" -"))
                spelled = (1900 if sm[1] == "nineteen" else 2000) + v if v is not None else None
            else:
                spelled = 2000 + (_words_to_int(sm[3]) or 0 if sm[3] else 0)
            if spelled is not None:
                year = _valid_year(spelled, today) or year

    month: int | None = None
    month_span: tuple[int, int] | None = None
    for mm in re.finditer(rf"\b({_MONTH_RE})\b\.?", t):
        name = mm[1]
        if name in _AMBIGUOUS_MONTHS:
            around = t[max(0, mm.start() - 14) : mm.end() + 14]
            if not re.search(rf"\d|{_ORD_RE}", around):
                continue
        month, month_span = _MONTHS[name], mm.span()

    day: int | None = None
    if month_span is not None:
        after = t[month_span[1] :]
        before = t[: month_span[0]]
        a = re.match(rf"\s*(?:the\s+)?{_DAY_TOKEN}", after)
        b = re.search(rf"{_DAY_TOKEN}\s*(?:of\s+)?(?:the\s+month\s+of\s+)?$", before)
        for cand in (a, b):
            if cand and day is None:
                day = _day_value(cand[1], cand[2])  # (?!\d) keeps "April 1988" from reading 19 as a day
    if day is None:
        sm2 = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)\b|\bthe\s+(\d{{1,2}})(?!\d)|\bthe\s+({_ORD_RE})\b", t)
        if sm2:
            day = _day_value(sm2[1] or sm2[2], sm2[3])
    return DobParts(year, month, day)


_JA_NUM_DOB = r"([0-9〇零一二三四五六七八九十百千]+|元)"


def _ja_int(s: str) -> int | None:
    if s == "元":
        return 1
    if s and all(ch in _KANJI_DIGITS for ch in s) and len(s) > 1:  # 一九八八
        return int("".join(str(_KANJI_DIGITS[ch]) for ch in s))
    return _ja_number(s)


def _dob_ja(t: str, today: date) -> DobParts:
    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", t)
    if m:
        return DobParts(_valid_year(int(m[1]), today), int(m[2]) if 1 <= int(m[2]) <= 12 else None,
                        int(m[3]) if 1 <= int(m[3]) <= 31 else None)  # fmt: skip
    era = {"昭和": 1925, "平成": 1988, "令和": 2018}
    year = month = day = None
    em = re.search(r"(昭和|平成|令和)" + _JA_NUM_DOB + "年", t)
    if em:
        n = _ja_int(em[2])
        year = _valid_year(era[em[1]] + n, today) if n else None
    else:
        ym = re.search(_JA_NUM_DOB + "年", t)
        n = _ja_int(ym[1]) if ym else None
        year = _valid_year(n, today) if n else None
    mm = re.search(r"(?<![ヶかカケ])" + _JA_NUM_DOB + "月(?!曜)", t)
    if mm:
        n = _ja_int(mm[1])
        month = n if n and 1 <= n <= 12 else None
    dm = re.search(_JA_NUM_DOB + "日(?!間)", t)
    if dm:
        n = _ja_int(dm[1])
        day = n if n and 1 <= n <= 31 else None
    return DobParts(year, month, day)


def parse_dob(text: str, language: Language, today: date) -> DobParts:
    """Year / month / day the caller explicitly said. Missing parts stay None."""
    t = normalise(text)
    return _dob_ja(t, today) if _has_japanese(t) else _dob_en(t, today)


def safety_intents(text: str, language: Language) -> list[Action]:
    """Caller-rights intents detected deterministically (used alongside the LLM)."""
    t = normalise(text)
    is_ja = _has_japanese(t)  # script-based: callers code-switch between languages
    out = []
    for act in (Action.STOP_CONTACT, Action.REQUEST_HUMAN):
        if is_ja:
            if any(re.search(k, t) for k in _JA[act]):
                out.append(act)
        elif any(re.search(p, t) for p in _EN[act]):
            out.append(act)
    return out
