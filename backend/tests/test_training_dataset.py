"""The post-training dataset is reproducible, leak-free and schema-valid.

The held-out file is frozen: `build()` must reproduce the committed files byte for byte,
so a change to the generator that touches held-out rows is a deliberate, reviewed event.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from app.domain.commands import Action, Interpretation
from app.domain.models import DialogPhase
from app.domain.turn_context import context_for
from app.training import build_dataset as bd

DATA = bd.DATA_DIR


def _load(name: str) -> list[dict]:
    p = DATA / f"{name}.jsonl"
    assert p.exists(), f"{p} missing: run `python -m app.training.build_dataset`"
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture(scope="module")
def built() -> tuple[list[dict], list[dict]]:
    return bd.build()


def test_committed_files_match_generator(built: tuple[list[dict], list[dict]]) -> None:
    train, heldout = built
    assert _load("train") == train
    assert _load("heldout") == heldout


def test_sizes(built: tuple[list[dict], list[dict]]) -> None:
    train, heldout = built
    assert len(train) >= 450
    assert 90 <= len(heldout) <= 140


def test_no_leakage_between_splits(built: tuple[list[dict], list[dict]]) -> None:
    train, heldout = built
    train_keys = {(r["utterance"], r["phase"]) for r in train}
    for r in heldout:
        assert (r["utterance"], r["phase"]) not in train_keys, r["id"]
    ids = [r["id"] for r in train + heldout]
    assert len(ids) == len(set(ids))


def test_every_category_and_language_in_both_splits(built: tuple[list[dict], list[dict]]) -> None:
    train, heldout = built
    assert set(r["category"] for r in train) == set(r["category"] for r in heldout)
    for rows in (train, heldout):
        langs = Counter(r["language"] for r in rows)
        assert langs["en"] > 0 and langs["ja"] > 0


def test_gold_is_schema_valid_and_phase_consistent(built: tuple[list[dict], list[dict]]) -> None:
    train, heldout = built
    for r in train + heldout:
        interp = Interpretation.model_validate({"actions": r["gold"]["actions"]})  # raises on bad shape
        assert 1 <= len(interp.actions) <= 3, r["id"]
        ctx = context_for(DialogPhase(r["phase"]))
        assert r["expected_slot"] == ctx.expected_slot.value
        assert r["allowed_actions"] == sorted(a.value for a in ctx.allowed)
        for a in interp.actions:
            assert a.action in ctx.allowed, (r["id"], a.action)
        assert r["last_agent"], r["id"]


def test_dob_phase_never_labels_money(built: tuple[list[dict], list[dict]]) -> None:
    """The real-call regression: while a DOB is expected, digits are never a payment."""
    train, heldout = built
    for r in train + heldout:
        if r["phase"] == DialogPhase.IDENTITY_DOB.value:
            assert all(a["action"] != Action.PROPOSE_PAYMENT.value for a in r["gold"]["actions"]), r["id"]
            for a in r["gold"]["actions"]:
                assert "amount" not in a, r["id"]


def test_partial_dob_never_padded(built: tuple[list[dict], list[dict]]) -> None:
    train, heldout = built
    for r in train + heldout:
        for a in r["gold"]["actions"]:
            if a["action"] == Action.PARTIAL_DOB.value:
                assert "dob" not in a
                assert any(k in a for k in ("dob_year", "dob_month", "dob_day")), r["id"]
                # a complete y/m/d is a PROVIDE_DOB, not a partial
                assert not all(k in a for k in ("dob_year", "dob_month", "dob_day")), r["id"]


def test_caller_rights_present_in_every_phase(built: tuple[list[dict], list[dict]]) -> None:
    train, _ = built
    for act in (Action.STOP_CONTACT.value, Action.REQUEST_HUMAN.value):
        phases = {r["phase"] for r in train if any(a["action"] == act for a in r["gold"]["actions"])}
        assert {"GREETING", "IDENTITY_DOB", "NEGOTIATION", "CONFIRMATION"} <= phases, act


def test_write_is_deterministic(tmp_path: Path, built: tuple[list[dict], list[dict]]) -> None:
    train, heldout = built
    bd.write(train, heldout, tmp_path)
    bd.write(train, heldout, tmp_path / "again")
    for name in ("train", "heldout"):
        assert (tmp_path / f"{name}.jsonl").read_bytes() == (tmp_path / "again" / f"{name}.jsonl").read_bytes()
        assert (tmp_path / f"{name}.jsonl").read_bytes() == (DATA / f"{name}.jsonl").read_bytes()


def test_number_helpers() -> None:
    assert bd.en_words(30_000) == "thirty thousand"
    assert bd.en_words(25_000) == "twenty-five thousand"
    assert bd.en_words(120_000) == "one hundred twenty thousand"
    assert bd.ja_kanji_amount(30_000) == "三万円"
    assert bd.ja_kanji_amount(15_000) == "一万五千円"
    assert bd.ja_kanji_amount(120_000) == "12万円"
    assert bd.spelled_year(1988) == "nineteen eighty-eight"
    assert bd.spelled_year(2001) == "twenty oh one"
    assert bd.wareki(1988) == "昭和63年"
    assert bd.wareki(1989) == "平成元年"
    assert bd.wareki(1990) == "平成2年"
