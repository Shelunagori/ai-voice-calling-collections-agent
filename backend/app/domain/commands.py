"""Typed proposals produced by the language layer.

The LLM (or the deterministic parser) may only *propose* these. They carry no
authority: `ConversationController.apply()` validates each one through the policy
engine before any state changes. Unknown fields are rejected, numbers are bounded,
and free text is length-limited, so malformed model output fails closed.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Action(StrEnum):
    AFFIRM = "AFFIRM"  # "yes", "that's right", "はい"
    DENY = "DENY"  # "no"
    PROVIDE_DOB = "PROVIDE_DOB"  # a complete date of birth the caller actually said
    PARTIAL_DOB = "PARTIAL_DOB"  # some of year / month / day; missing parts are never invented
    WRONG_PERSON = "WRONG_PERSON"  # caller is not the account holder
    PROPOSE_PAYMENT = "PROPOSE_PAYMENT"  # amount and/or date
    CANNOT_PAY = "CANNOT_PAY"
    REQUEST_DISCOUNT = "REQUEST_DISCOUNT"
    ASK_BALANCE = "ASK_BALANCE"
    ASK_PURPOSE = "ASK_PURPOSE"
    REQUEST_HUMAN = "REQUEST_HUMAN"
    STOP_CONTACT = "STOP_CONTACT"
    DISPUTE = "DISPUTE"
    GOODBYE = "GOODBYE"
    UNCLEAR = "UNCLEAR"


class ProposedAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: Action
    amount: int | None = Field(default=None, ge=0, le=100_000_000)
    date: dt.date | None = None
    days_from_now: int | None = Field(default=None, ge=0, le=3650)
    dob: dt.date | None = None  # complete date only (PROVIDE_DOB)
    # PARTIAL_DOB components. A complete-but-impossible date (April 31) is carried here too,
    # so the controller can say "that isn't a valid date" instead of guessing.
    dob_year: int | None = Field(default=None, ge=1900, le=2100)
    dob_month: int | None = Field(default=None, ge=1, le=12)
    dob_day: int | None = Field(default=None, ge=1, le=31)
    note: str | None = Field(default=None, max_length=200)

    @field_validator("note")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        return v.strip()[:200] if v else v

    @model_validator(mode="after")
    def _dob_shape(self) -> ProposedAction:
        parts = (self.dob_year, self.dob_month, self.dob_day)
        if self.action == Action.PARTIAL_DOB:
            if self.dob is not None:
                raise ValueError("PARTIAL_DOB carries components, not a complete dob")
            if all(p is None for p in parts):
                raise ValueError("PARTIAL_DOB needs at least one of dob_year / dob_month / dob_day")
        elif any(p is not None for p in parts):
            raise ValueError("dob_year/dob_month/dob_day are only valid on PARTIAL_DOB")
        return self


class Interpretation(BaseModel):
    """Structured reading of one caller turn. Max three actions per turn."""

    model_config = ConfigDict(extra="forbid")

    actions: list[ProposedAction] = Field(default_factory=list, max_length=3)
    source: str = "rules"  # "rules" | "llm" | "llm+rules"
    notes: list[str] = Field(default_factory=list, max_length=20)  # validation / grounding trail

    @property
    def names(self) -> list[Action]:
        return [a.action for a in self.actions]

    def has(self, action: Action) -> bool:
        return action in self.names

    def first(self, action: Action) -> ProposedAction | None:
        return next((a for a in self.actions if a.action == action), None)


def interpretation_json_schema(allowed: Iterable[Action] | None = None) -> dict[str, Any]:
    """Compact JSON schema handed to providers that support constrained output. With
    `allowed`, the action enum is restricted to what the current phase accepts."""
    keep = set(Action) if allowed is None else set(allowed)
    names = [a.value for a in Action if a in keep]
    return {
        "type": "object",
        "properties": {
            "actions": {
                "type": "array",
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": names},
                        "amount": {"type": ["integer", "null"]},
                        "date": {"type": ["string", "null"], "description": "ISO date YYYY-MM-DD"},
                        "days_from_now": {"type": ["integer", "null"]},
                        "dob": {
                            "type": ["string", "null"],
                            "description": "complete date of birth YYYY-MM-DD (PROVIDE_DOB only)",
                        },
                        "dob_year": {"type": ["integer", "null"]},
                        "dob_month": {"type": ["integer", "null"]},
                        "dob_day": {"type": ["integer", "null"]},
                        "note": {"type": ["string", "null"]},
                    },
                    "required": ["action"],
                },
            }
        },
        "required": ["actions"],
    }
