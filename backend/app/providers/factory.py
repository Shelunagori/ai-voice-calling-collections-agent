"""Build providers from settings. Missing credentials -> mock provider, never a crash."""

from __future__ import annotations

import logging
import time
from typing import Any

from ..config import Settings
from ..runtime import Providers
from .mock import FakeTelephony, MockLLM, MockNotifier, MockSTT, MockTTS

log = logging.getLogger(__name__)


def build_providers(s: Settings) -> Providers:
    labels: dict[str, str] = {}
    llm: Any
    if s.effective_llm_provider() == "cloudflare":
        from .cloudflare_llm import CloudflareLLM

        llm = CloudflareLLM(s.cloudflare_account_id, s.cloudflare_api_token, s.cloudflare_ai_model, s.llm_max_retries)
        labels["llm"] = f"cloudflare:{s.cloudflare_ai_model}"
    else:
        # In mock mode the rules parser is the language layer; no LLM object is used.
        llm = None
        labels["llm"] = "mock-rules"

    stt: Any
    if s.effective_stt_provider() == "cartesia":
        from .cartesia import CartesiaSTT

        stt = CartesiaSTT(s.cartesia_api_key, s.cartesia_stt_model, s.cartesia_version, time.monotonic)
        labels["stt"] = f"cartesia:{s.cartesia_stt_model}"
    else:
        stt = MockSTT(time.monotonic)
        labels["stt"] = "mock"

    tts: Any
    if s.effective_tts_provider() == "cartesia":
        from .cartesia import CartesiaTTS

        tts = CartesiaTTS(
            s.cartesia_api_key,
            s.cartesia_tts_model,
            s.cartesia_version,
            s.cartesia_voice_id,
            s.cartesia_voice_id_ja,
            s.audio_sample_rate,
        )
        labels["tts"] = f"cartesia:{s.cartesia_tts_model}"
    else:
        tts = MockTTS(s.audio_sample_rate)
        labels["tts"] = "mock"

    telephony: Any
    if s.telephony_active:
        from .twilio import TwilioTelephony

        telephony = TwilioTelephony(
            s.twilio_account_sid, s.twilio_auth_token, s.twilio_phone_number, s.twilio_webhook_base_url
        )
        labels["telephony"] = "twilio"
    else:
        telephony = FakeTelephony(auth_token=s.twilio_auth_token or "fake-token")
        labels["telephony"] = "fake"
    return Providers(llm=llm, stt=stt, tts=tts, telephony=telephony, notifier=MockNotifier(), labels=labels)


def build_judge_llm(s: Settings) -> Any:
    if s.effective_judge_provider() == "cloudflare":
        from .cloudflare_llm import CloudflareLLM

        return CloudflareLLM(
            s.cloudflare_account_id, s.cloudflare_api_token, s.cloudflare_judge_model or s.cloudflare_ai_model
        )
    return None


def voice_capable(p: Providers) -> bool:
    """Real speech in the browser needs a real STT; TTS may still be mock (text shown)."""
    return p.labels.get("stt", "mock") != "mock"


def mock_llm_for_tests() -> MockLLM:
    return MockLLM()
