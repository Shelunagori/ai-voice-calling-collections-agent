"""Persistence operations. All SQL is built with SQLAlchemy expressions and bound
parameters; nothing from model output is ever interpolated into SQL."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from ..domain.audit import AuditType
from ..domain.contact import contact_key, mask_number
from ..domain.models import AccountTerms, DebtorProfile, Language
from ..domain.scenarios import SCENARIOS
from ..voice.latency import summarize
from . import tables as t

log = logging.getLogger(__name__)


def _uuid(v: Any) -> uuid.UUID | None:
    if v is None or isinstance(v, uuid.UUID):
        return v
    return uuid.UUID(str(v))


def _dt(v: Any) -> datetime:
    if isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v))


class Repository:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    # ------------------------------------------------------------------ seed
    async def seed_demo_data(self, reset: bool = False) -> int:
        """Idempotently upsert the synthetic scenarios. `reset` restores account flags."""
        n = 0
        async with self.engine.begin() as conn:
            for key, sc in SCENARIOS.items():
                d, a = sc.debtor, sc.account
                exists = (await conn.execute(sa.select(t.debtors.c.id).where(t.debtors.c.id == d.debtor_id))).first()
                if not exists:
                    await conn.execute(
                        t.debtors.insert().values(
                            id=d.debtor_id,
                            full_name=d.full_name,
                            display_name_ja=d.display_name_ja,
                            name_aliases=list(d.name_aliases),
                            date_of_birth=d.date_of_birth,
                            phone_e164=d.phone_e164,
                            preferred_language=d.preferred_language.value,
                            timezone=d.timezone,
                            synthetic=True,
                        )
                    )
                acc = (
                    await conn.execute(
                        sa.select(t.collection_accounts.c.id).where(t.collection_accounts.c.id == a.account_id)
                    )
                ).first()
                values = dict(
                    debtor_id=d.debtor_id,
                    scenario_key=key,
                    creditor_name=a.creditor_name,
                    outstanding_balance=a.outstanding_balance,
                    currency=a.currency,
                    allowed_min_payment=a.allowed_min_payment,
                    max_extension_days=a.max_extension_days,
                    discount_authority=a.discount_authority,
                )
                if not acc:
                    await conn.execute(
                        t.collection_accounts.insert().values(
                            id=a.account_id, contact_attempts=0, stop_contact=False, **values
                        )
                    )
                    n += 1
                elif reset:
                    await conn.execute(
                        t.collection_accounts.update()
                        .where(t.collection_accounts.c.id == a.account_id)
                        .values(contact_attempts=0, stop_contact=False, stop_contact_at=None, **values)
                    )
            if reset:
                await conn.execute(t.debtors.update().values(stop_contact=False, stop_contact_at=None))
                await conn.execute(t.contact_points.delete())
        return n

    async def get_account(self, scenario_key: str) -> tuple[DebtorProfile, AccountTerms] | None:
        q = (
            sa.select(t.collection_accounts, t.debtors)
            .join(t.debtors, t.debtors.c.id == t.collection_accounts.c.debtor_id)
            .where(t.collection_accounts.c.scenario_key == scenario_key)
        )
        async with self.engine.connect() as conn:
            row = (await conn.execute(q)).mappings().first()
        if not row:
            return None
        debtor = DebtorProfile(
            debtor_id=row["debtor_id"],
            full_name=row["full_name"],
            name_aliases=tuple(row["name_aliases"]),
            date_of_birth=row["date_of_birth"],
            phone_e164=row["phone_e164"],
            preferred_language=Language(row["preferred_language"]),
            timezone=row["timezone"],
            display_name_ja=row["display_name_ja"],
        )
        acc = AccountTerms(
            account_id=_uuid(row["id"]) or uuid.uuid4(),
            debtor_id=row["debtor_id"],
            creditor_name=row["creditor_name"],
            outstanding_balance=row["outstanding_balance"],
            currency=row["currency"],
            allowed_min_payment=row["allowed_min_payment"],
            max_extension_days=row["max_extension_days"],
            discount_authority=row["discount_authority"],
            contact_attempts=row["contact_attempts"],
            stop_contact=row["stop_contact"],
        )
        return debtor, acc

    async def list_accounts(self) -> list[dict[str, Any]]:
        """Per-account contact eligibility. `eligible` covers the account and debtor
        scopes; a contact-point stop is per destination (see list_contact_points)."""
        a, d = t.collection_accounts, t.debtors
        q = (
            sa.select(
                a.c.scenario_key,
                a.c.contact_attempts,
                a.c.stop_contact,
                a.c.stop_contact_at,
                d.c.stop_contact.label("debtor_stop_contact"),
                d.c.stop_contact_at.label("debtor_stop_contact_at"),
                d.c.full_name.label("debtor_name"),
            )
            .join(d, d.c.id == a.c.debtor_id)
            .order_by(a.c.scenario_key)
        )
        async with self.engine.connect() as conn:
            rows = (await conn.execute(q)).mappings().all()
        out = []
        for r in rows:
            row = dict(r)
            for k in ("stop_contact_at", "debtor_stop_contact_at"):
                row[k] = row[k].isoformat() if row[k] else None
            row["eligible"] = not (row["stop_contact"] or row["debtor_stop_contact"])
            out.append(row)
        return out

    async def list_contact_points(self) -> list[dict[str, Any]]:
        q = sa.select(t.contact_points.c.label, t.contact_points.c.stop_contact, t.contact_points.c.stop_contact_at)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(q.order_by(t.contact_points.c.created_at))).mappings().all()
        return [
            {**dict(r), "stop_contact_at": r["stop_contact_at"].isoformat() if r["stop_contact_at"] else None}
            for r in rows
        ]

    async def stop_contact_scopes(self, debtor_id: uuid.UUID, destination: str | None) -> dict[str, bool]:
        """Debtor- and contact-point-level stop-contact flags for an outbound call."""
        async with self.engine.connect() as conn:
            debtor = (
                await conn.execute(sa.select(t.debtors.c.stop_contact).where(t.debtors.c.id == debtor_id))
            ).scalar()
            cp = None
            if destination:
                cp = (
                    await conn.execute(
                        sa.select(t.contact_points.c.stop_contact).where(
                            t.contact_points.c.key == contact_key(destination)
                        )
                    )
                ).scalar()
        return {"debtor": bool(debtor), "contact_point": bool(cp)}

    async def increment_contact_attempts(self, account_id: uuid.UUID) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                t.collection_accounts.update()
                .where(t.collection_accounts.c.id == account_id)
                .values(contact_attempts=t.collection_accounts.c.contact_attempts + 1)
            )

    # ------------------------------------------------------------------ sessions
    async def create_session(
        self,
        *,
        session_id: uuid.UUID,
        account: AccountTerms,
        scenario_key: str,
        channel: str,
        language: str,
        input_mode: str,
        providers: dict[str, str],
        state: dict[str, Any],
        started_at: datetime,
        retention_days: int,
        call_id: str | None = None,
    ) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                t.voice_sessions.insert().values(
                    id=session_id,
                    account_id=account.account_id,
                    debtor_id=account.debtor_id,
                    scenario_key=scenario_key,
                    channel=channel,
                    language=language,
                    input_mode=input_mode,
                    call_id=call_id,
                    call_status=state["call_status"],
                    voice_state="IDLE",
                    identity_status=state["identity_status"],
                    promise_status=state["promise_status"],
                    providers=providers,
                    state=state,
                    started_at=started_at,
                    transcript_expires_at=started_at + timedelta(days=retention_days),
                )
            )

    async def set_call_id(self, session_id: uuid.UUID, call_id: str) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                t.voice_sessions.update().where(t.voice_sessions.c.id == session_id).values(call_id=call_id)
            )

    async def find_session_by_call(self, call_id: str) -> dict[str, Any] | None:
        async with self.engine.connect() as conn:
            row = (
                (await conn.execute(sa.select(t.voice_sessions).where(t.voice_sessions.c.call_id == call_id)))
                .mappings()
                .first()
            )
        return dict(row) if row else None

    async def update_session_status(self, session_id: uuid.UUID, **values: Any) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(t.voice_sessions.update().where(t.voice_sessions.c.id == session_id).values(**values))

    async def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        cols = [
            t.voice_sessions.c.id,
            t.voice_sessions.c.scenario_key,
            t.voice_sessions.c.channel,
            t.voice_sessions.c.language,
            t.voice_sessions.c.call_status,
            t.voice_sessions.c.identity_status,
            t.voice_sessions.c.promise_status,
            t.voice_sessions.c.stop_contact,
            t.voice_sessions.c.human_transfer_requested,
            t.voice_sessions.c.ended_reason,
            t.voice_sessions.c.started_at,
            t.voice_sessions.c.ended_at,
        ]
        q = sa.select(*cols).order_by(t.voice_sessions.c.started_at.desc()).limit(min(limit, 100))
        async with self.engine.connect() as conn:
            rows = (await conn.execute(q)).mappings().all()
        return [_json_row(r) for r in rows]

    async def get_session_detail(self, session_id: uuid.UUID) -> dict[str, Any] | None:
        async with self.engine.connect() as conn:
            s = (
                (await conn.execute(sa.select(t.voice_sessions).where(t.voice_sessions.c.id == session_id)))
                .mappings()
                .first()
            )
            if not s:
                return None
            turns = (
                await conn.execute(
                    sa.select(t.conversation_turns)
                    .where(t.conversation_turns.c.session_id == session_id)
                    .order_by(t.conversation_turns.c.seq)
                )
            ).mappings()
            events = (
                await conn.execute(
                    sa.select(t.call_events)
                    .where(t.call_events.c.session_id == session_id)
                    .order_by(t.call_events.c.at)
                )
            ).mappings()
            promise = (
                (await conn.execute(sa.select(t.payment_promises).where(t.payment_promises.c.session_id == session_id)))
                .mappings()
                .first()
            )
            lat = (
                await conn.execute(sa.select(t.turn_latencies).where(t.turn_latencies.c.session_id == session_id))
            ).mappings()
            return {
                "session": _json_row(s),
                "turns": [_json_row(r) for r in turns],
                "audit": [_json_row(r) for r in events],
                "promise": _json_row(promise) if promise else None,
                "latency": [_json_row(r) for r in lat],
            }

    # ------------------------------------------------------------------ writes from the runtime
    async def insert_turn(self, session_id: uuid.UUID, payload: dict[str, Any], speaker: str, at: datetime) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                t.conversation_turns.insert().values(
                    id=uuid.uuid4(),
                    session_id=session_id,
                    seq=payload["index"],
                    speaker=speaker,
                    text=payload.get("text"),
                    spoken_text=payload.get("spoken_text"),
                    interrupted=bool(payload.get("interrupted", False)),
                    acts=(payload.get("acts") or "")[:200] or None,
                    controller_turn=payload.get("turn_index"),
                    interpretation=payload.get("interpretation"),
                    created_at=at,
                )
            )

    async def insert_audit(self, payload: dict[str, Any]) -> None:
        sid = _uuid(payload["session_id"])
        at = _dt(payload["at"])
        async with self.engine.begin() as conn:
            await conn.execute(
                t.call_events.insert().values(
                    id=_uuid(payload["event_id"]),
                    session_id=sid,
                    type=payload["type"],
                    turn_index=payload.get("turn_index"),
                    data=payload.get("data", {}),
                    at=at,
                )
            )
            data = payload.get("data", {})
            if payload["type"] == AuditType.POLICY_DECISION.value:
                await conn.execute(
                    t.policy_decisions.insert().values(
                        id=_uuid(data["decision_id"]),
                        session_id=sid,
                        rule=data["rule"],
                        decision=data["decision"],
                        reason=data["reason"],
                        details=data.get("details", {}),
                        turn_index=payload.get("turn_index"),
                        decided_at=_dt(data["timestamp"]),
                    )
                )

    async def insert_promise(
        self, session_id: uuid.UUID, account_id: uuid.UUID, data: dict[str, Any], at: datetime
    ) -> bool:
        """Insert the confirmed promise. The UNIQUE(session_id) constraint makes this
        idempotent even if the event is delivered twice."""
        try:
            async with self.engine.begin() as conn:
                await conn.execute(
                    t.payment_promises.insert().values(
                        id=_uuid(data["promise_id"]),
                        session_id=session_id,
                        account_id=account_id,
                        amount=data["amount"],
                        currency=data["currency"],
                        due_date=date.fromisoformat(data["due_date"]),
                        confirmation_turn=data["confirmation_turn"],
                        confirmed_at=at,
                        policy_decision_ids=data["policy_decision_ids"],
                    )
                )
            return True
        except IntegrityError:
            return False

    async def set_stop_contact(
        self,
        account_id: uuid.UUID,
        at: datetime,
        *,
        debtor_id: uuid.UUID | None = None,
        contact: str | None = None,
    ) -> None:
        """Persist a stop-contact request for the account, its debtor (every account of
        that debtor) and, on phone calls, the contact point. Idempotent; the first
        timestamp is kept."""
        async with self.engine.begin() as conn:
            if debtor_id is None:
                debtor_id = (
                    await conn.execute(
                        sa.select(t.collection_accounts.c.debtor_id).where(t.collection_accounts.c.id == account_id)
                    )
                ).scalar()
            accounts = t.collection_accounts
            await conn.execute(
                accounts.update()
                .where(sa.or_(accounts.c.id == account_id, accounts.c.debtor_id == debtor_id))
                .where(accounts.c.stop_contact == sa.false())
                .values(stop_contact=True, stop_contact_at=at)
            )
            if debtor_id is not None:
                await conn.execute(
                    t.debtors.update()
                    .where(t.debtors.c.id == debtor_id, t.debtors.c.stop_contact == sa.false())
                    .values(stop_contact=True, stop_contact_at=at)
                )
            if contact:
                key = contact_key(contact)
                cp = t.contact_points
                exists = (await conn.execute(sa.select(cp.c.stop_contact).where(cp.c.key == key))).first()
                if exists is None:
                    await conn.execute(
                        cp.insert().values(key=key, label=mask_number(contact), stop_contact=True, stop_contact_at=at)
                    )
                elif not exists[0]:
                    await conn.execute(cp.update().where(cp.c.key == key).values(stop_contact=True, stop_contact_at=at))

    async def insert_latency(self, session_id: uuid.UUID, payload: dict[str, Any], at: datetime) -> None:
        providers = payload.get("providers") or {}
        mode = ",".join(f"{k}={v}" for k, v in sorted(providers.items())) or "unknown"
        async with self.engine.begin() as conn:
            await conn.execute(
                t.turn_latencies.insert().values(
                    id=uuid.uuid4(),
                    session_id=session_id,
                    turn_index=payload["turn_index"],
                    input_mode=payload["input_mode"],
                    provider_mode=mode[:64],
                    stages=payload["stages"],
                    created_at=at,
                )
            )

    # ------------------------------------------------------------------ metrics
    async def latency_summary(self, limit: int = 2000) -> dict[str, Any]:
        q = (
            sa.select(t.turn_latencies.c.provider_mode, t.turn_latencies.c.input_mode, t.turn_latencies.c.stages)
            .order_by(t.turn_latencies.c.created_at.desc())
            .limit(limit)
        )
        async with self.engine.connect() as conn:
            rows = (await conn.execute(q)).all()
        groups: dict[str, dict[str, list[float]]] = {}
        for mode, input_mode, stages in rows:
            g = groups.setdefault(f"{mode}|input={input_mode}", {})
            for k, v in (stages or {}).items():
                g.setdefault(k, []).append(float(v))
        return {k: summarize(v) for k, v in groups.items()}

    # ------------------------------------------------------------------ retention
    async def purge_expired_transcripts(self, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        async with self.engine.begin() as conn:
            expired = sa.select(t.voice_sessions.c.id).where(t.voice_sessions.c.transcript_expires_at < now)
            res = await conn.execute(
                t.conversation_turns.update()
                .where(t.conversation_turns.c.session_id.in_(expired), t.conversation_turns.c.text.is_not(None))
                .values(text=None, spoken_text=None, interpretation=None)
            )
            return res.rowcount or 0

    # ------------------------------------------------------------------ telephony idempotency
    async def record_webhook_once(self, key: str, call_id: str, kind: str, payload: dict[str, Any]) -> bool:
        try:
            async with self.engine.begin() as conn:
                await conn.execute(
                    t.telephony_webhooks.insert().values(
                        id=key[:128], call_id=call_id, kind=kind, payload=payload, received_at=datetime.now(UTC)
                    )
                )
            return True
        except IntegrityError:
            return False

    async def ping(self) -> None:
        async with self.engine.connect() as conn:
            await conn.execute(sa.text("SELECT 1"))


def _json_row(r: Any) -> dict[str, Any]:
    out = {}
    for k, v in dict(r).items():
        if isinstance(v, uuid.UUID):
            v = str(v)
        elif isinstance(v, datetime | date):
            v = v.isoformat()
        out[k] = v
    return out


class DbRecorder:
    """Persists session events off the hot path via its own queue and writer task."""

    def __init__(
        self,
        repo: Repository,
        session_id: uuid.UUID,
        account_id: uuid.UUID,
        now: Any,
        *,
        debtor_id: uuid.UUID | None = None,
        contact: str | None = None,
    ) -> None:
        self.repo = repo
        self.session_id = session_id
        self.account_id = account_id
        self.debtor_id = debtor_id
        self.contact = contact  # phone number of the person on the call (never persisted in clear)
        self._now = now
        self._q: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue(maxsize=5000)
        self._task: asyncio.Task[None] | None = None
        self.failures = 0

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def record(self, kind: str, payload: dict[str, Any]) -> None:
        if kind in {"audit", "turn.caller", "turn.agent", "state", "latency", "session.ended", "lifecycle"}:
            try:
                self._q.put_nowait((kind, payload))
            except asyncio.QueueFull:
                self.failures += 1

    async def close(self, timeout: float = 5.0) -> None:
        if self._task is None:
            return
        await self._q.put(None)
        try:
            await asyncio.wait_for(self._task, timeout)
        except TimeoutError:
            self._task.cancel()

    async def _run(self) -> None:
        while True:
            item = await self._q.get()
            if item is None:
                return
            try:
                await self._write(*item)
            except Exception:
                self.failures += 1
                log.exception("db_recorder_write_failed", extra={"kind": item[0]})

    async def _write(self, kind: str, p: dict[str, Any]) -> None:
        now = self._now()
        sid = self.session_id
        if kind == "audit":
            await self.repo.insert_audit(p)
            data = p.get("data", {})
            if p["type"] == AuditType.PROMISE_CONFIRMED.value:
                await self.repo.insert_promise(
                    sid,
                    self.account_id,
                    {**data, "confirmation_turn": p.get("turn_index") or 0},
                    _dt(p["at"]),
                )
            elif p["type"] == AuditType.STOP_CONTACT_REQUESTED.value:
                await self.repo.set_stop_contact(
                    self.account_id, _dt(p["at"]), debtor_id=self.debtor_id, contact=self.contact
                )
        elif kind == "turn.caller":
            await self.repo.insert_turn(sid, p, "caller", now)
        elif kind == "turn.agent":
            await self.repo.insert_turn(sid, p, "agent", now)
        elif kind == "state":
            c = p["collection"]
            await self.repo.update_session_status(
                sid,
                state=c,
                voice_state=p.get("voice_state", "UNKNOWN"),
                call_status=c["call_status"],
                identity_status=c["identity_status"],
                promise_status=c["promise_status"],
                stop_contact=c["stop_contact"],
                human_transfer_requested=c["human_transfer_requested"],
                transfer_status=c["transfer_status"],
                ended_reason=c.get("ended_reason"),
            )
        elif kind == "lifecycle":
            await self.repo.update_session_status(sid, voice_state=p["to"])
        elif kind == "latency":
            await self.repo.insert_latency(sid, p, now)
        elif kind == "session.ended":
            await self.repo.update_session_status(sid, summary=p.get("summary"), ended_at=now)
