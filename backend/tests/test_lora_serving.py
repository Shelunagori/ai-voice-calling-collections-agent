"""Serving the post-trained adapter through Cloudflare Workers AI BYO LoRA (raw-prompt mode)."""

from __future__ import annotations

import json

import httpx
import pytest

from app.config import Settings
from app.evaluation import nlu_bench as nb
from app.providers.cloudflare_llm import CloudflareLLM
from app.providers.factory import build_providers
from app.training.format import build_prompt


def cf(handler, lora: str | None) -> CloudflareLLM:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return CloudflareLLM("acct", "tok", "@cf/google/gemma-2b-it-lora", 1, client=client, lora=lora)


async def test_lora_request_is_raw_prompt_with_adapter_and_completion_is_parsed() -> None:
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["body"] = json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "success": True,
                "result": {"response": '{"actions":[{"action":"PROPOSE_PAYMENT","amount":30000}]}<end_of_turn> thanks'},
            },
        )

    out = await cf(handler, "vca-nlu-gemma-2b-v2").complete_json(
        "SYS", "3万円払えます。", {"type": "object"}, timeout=2
    )
    assert out == {"actions": [{"action": "PROPOSE_PAYMENT", "amount": 30000}]}
    assert seen["url"].endswith("/ai/run/@cf/google/gemma-2b-it-lora")
    body = seen["body"]
    assert body["lora"] == "vca-nlu-gemma-2b-v2" and body["raw"] is True
    assert body["prompt"] == build_prompt("SYS", "3万円払えます。")  # byte-for-byte the training format
    assert "messages" not in body and "response_format" not in body


async def test_without_lora_the_json_mode_request_is_unchanged() -> None:
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"success": True, "result": {"response": {"actions": []}}})

    await cf(handler, None).complete_json("SYS", "u", {"type": "object"}, timeout=2)
    assert "messages" in seen["body"] and "lora" not in seen["body"] and "raw" not in seen["body"]


async def test_lora_completion_without_json_is_a_bad_response() -> None:
    from app.providers.base import ErrorKind, ProviderError

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"success": True, "result": {"response": "I cannot help with that.<end_of_turn>"}}
        )

    with pytest.raises(ProviderError) as e:
        await cf(handler, "x").complete_json("s", "u", {}, timeout=2)
    assert e.value.kind == ErrorKind.BAD_RESPONSE


def test_factory_uses_the_lora_base_model_when_an_adapter_is_configured() -> None:
    s = Settings(
        _env_file=None,
        llm_provider="cloudflare",
        cloudflare_account_id="a",
        cloudflare_api_token="t",
        cloudflare_ai_lora="vca-nlu-gemma-2b-v2",
    )
    p = build_providers(s)
    assert p.llm is not None and p.llm.model == "@cf/google/gemma-2b-it-lora" and p.llm.lora == "vca-nlu-gemma-2b-v2"
    assert p.labels["llm"] == "cloudflare:@cf/google/gemma-2b-it-lora+lora:vca-nlu-gemma-2b-v2"
    s2 = Settings(_env_file=None, llm_provider="cloudflare", cloudflare_account_id="a", cloudflare_api_token="t")
    p2 = build_providers(s2)
    assert p2.llm.model == "@cf/meta/llama-3.3-70b-instruct-fp8-fast" and p2.llm.lora is None


def test_bench_treats_an_empty_action_list_as_invalid() -> None:
    s = nb.score([{"action": "AFFIRM"}], [])
    assert s["valid"] is False and s["action_match"] is False
