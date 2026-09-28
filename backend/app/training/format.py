"""Prompt / target formatting shared by the trainer, the offline evaluator and the serving
adapter. Importable without torch or transformers.

The fine-tuned model is served through Cloudflare Workers AI with `raw: true`, i.e. we send
the *exact* string below and no chat template is applied server-side. Training uses the
same string, so train == serve byte for byte. The system text is the production prompt
from `understanding.build_system_prompt`; Gemma has no system role, so it is folded into
the user turn.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from ..domain.models import DialogPhase, Language
from ..domain.turn_context import context_for
from ..domain.understanding import build_system_prompt

GEMMA_USER = "<start_of_turn>user\n{content}<end_of_turn>\n<start_of_turn>model\n"
GEMMA_END = "<end_of_turn>"
_JSON = re.compile(r"\{.*\}", re.S)


def system_for(rec: dict[str, Any]) -> str:
    ctx = context_for(DialogPhase(rec["phase"]))
    return build_system_prompt(ctx, date.fromisoformat(rec["today"]), Language(rec["language"]), rec["last_agent"])


def build_prompt(system: str, utterance: str) -> str:
    """The generation prompt (without <bos>; the tokenizer adds it)."""
    return GEMMA_USER.format(content=f"{system}\n\nCaller: {utterance[:500]}")


def prompt_for(rec: dict[str, Any]) -> str:
    return build_prompt(system_for(rec), rec["utterance"])


def target_for(rec: dict[str, Any]) -> str:
    """Compact JSON the model must produce, followed by the end-of-turn marker."""
    return json.dumps({"actions": rec["gold"]["actions"]}, ensure_ascii=False, separators=(",", ":")) + GEMMA_END


def parse_model_json(text: str) -> dict[str, Any]:
    """Extract the first JSON object from a completion (tolerates trailing markers / prose)."""
    text = text.split(GEMMA_END)[0]
    m = _JSON.search(text)
    if not m:
        raise ValueError("no JSON object in completion")
    obj = json.loads(m.group(0))
    if not isinstance(obj, dict):
        raise ValueError("completion is not a JSON object")
    return obj
