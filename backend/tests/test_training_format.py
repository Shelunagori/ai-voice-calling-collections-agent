"""Train-time formatting round-trips through the runtime validator and matches the runtime prompt."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest

from app.domain.commands import Interpretation
from app.domain.models import DialogPhase, Language
from app.domain.turn_context import constrain, context_for
from app.domain.understanding import build_system_prompt, validate_llm_output
from app.evaluation.nlu_bench import compact
from app.training import build_dataset as bd
from app.training import format as fmt

REC = {
    "id": "x",
    "phase": "NEGOTIATION",
    "language": "ja",
    "today": "2026-10-01",
    "last_agent": "いつ、おいくらお支払いいただけますか。",
    "utterance": "2週間後に3万円払えます。",
    "gold": {"actions": [{"action": "PROPOSE_PAYMENT", "amount": 30000, "days_from_now": 14}]},
}


def test_prompt_embeds_the_runtime_system_prompt_verbatim() -> None:
    p = fmt.prompt_for(REC)
    system = build_system_prompt(
        context_for(DialogPhase.NEGOTIATION), date(2026, 10, 1), Language.JA, REC["last_agent"]
    )
    assert p.startswith("<start_of_turn>user\n" + system + "\n\nCaller: 2週間後に3万円払えます。")
    assert p.endswith("<end_of_turn>\n<start_of_turn>model\n")
    assert "<bos>" not in p  # the tokenizer adds it


def test_target_is_compact_json_and_parses_back() -> None:
    t = fmt.target_for(REC)
    assert t == '{"actions":[{"action":"PROPOSE_PAYMENT","amount":30000,"days_from_now":14}]}<end_of_turn>'
    assert fmt.parse_model_json(t) == {"actions": REC["gold"]["actions"]}
    assert fmt.parse_model_json('Sure! {"actions":[{"action":"AFFIRM"}]} <end_of_turn> junk') == {
        "actions": [{"action": "AFFIRM"}]
    }
    with pytest.raises(ValueError):
        fmt.parse_model_json("no json here")
    with pytest.raises(ValueError):
        fmt.parse_model_json("[1,2]")


def test_every_dataset_target_round_trips_through_the_runtime_validator() -> None:
    """What we teach the model is exactly what the runtime accepts, for every row."""
    train, heldout = bd.build()
    for rec in train + heldout:
        obj = fmt.parse_model_json(fmt.target_for(rec))
        interp = constrain(validate_llm_output(obj), context_for(DialogPhase(rec["phase"])))
        assert compact(interp.actions) == rec["gold"]["actions"], rec["id"]
        Interpretation.model_validate(obj)
        assert len(fmt.prompt_for(rec)) < 2500, rec["id"]  # fits the 640-token training window comfortably


def test_train_script_imports_without_torch_and_loads_data() -> None:
    path = Path(__file__).resolve().parents[1] / "training" / "train_lora.py"
    spec = importlib.util.spec_from_file_location("train_lora", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["train_lora"] = mod
    spec.loader.exec_module(mod)
    rows = mod.load_jsonl(mod.DATA / "heldout.jsonl")
    assert len(rows) >= 90 and json.dumps(rows[0]["gold"])
    assert mod.TARGET_MODULES == ["q_proj", "k_proj", "v_proj", "o_proj"]
