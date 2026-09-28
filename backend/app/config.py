"""Application settings.

Every external dependency is optional. Missing credentials degrade the capability
(mock provider, disabled telephony) instead of crashing the process.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Reads backend/.env or the repository-root .env (later files take priority).
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"), env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True
    )

    app_env: Literal["local", "test", "staging", "production"] = "local"
    port: int = 8000
    log_level: str = "INFO"

    # Persistence. Local default is a SQLite file so the deterministic system runs with
    # zero infrastructure; production uses PostgreSQL via DATABASE_URL.
    database_url: str = "sqlite+aiosqlite:///./local.db"
    auto_migrate: bool = True  # run `alembic upgrade head` on startup
    # Emergency/dev escape hatch ONLY: lets production/staging start on a non-durable
    # database (logged CRITICAL). Never on by default; not for normal production use.
    allow_ephemeral_database: bool = False
    seed_demo_data: bool = True
    transcript_retention_days: int = 30

    frontend_url: str = "http://localhost:3000"
    public_base_url: str = "http://localhost:8000"
    cors_extra_origins: str = ""

    # Provider selection
    llm_provider: Literal["mock", "cloudflare"] = "mock"
    stt_provider: Literal["mock", "cartesia"] = "mock"
    tts_provider: Literal["mock", "cartesia"] = "mock"
    response_mode: Literal["template", "llm"] = "template"
    judge_provider: Literal["mock", "cloudflare"] = "mock"

    # Cloudflare Workers AI
    cloudflare_account_id: str = ""
    cloudflare_api_token: str = ""
    cloudflare_ai_model: str = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
    # Optional BYO LoRA (post-trained NLU): when set, the LoRA base model + adapter replaces
    # `cloudflare_ai_model` for interpretation. Rules parser fallback/merge is unchanged.
    cloudflare_ai_lora: str = ""
    cloudflare_ai_lora_model: str = "@cf/google/gemma-2b-it-lora"
    cloudflare_judge_model: str = ""
    llm_timeout_s: float = 2.5
    llm_max_retries: int = 1

    # Cartesia
    cartesia_api_key: str = ""
    cartesia_version: str = "2026-08-14"
    cartesia_stt_model: str = "ink-whisper"
    cartesia_tts_model: str = "sonic-3"
    cartesia_voice_id: str = ""
    cartesia_voice_id_ja: str = ""

    # Twilio
    telephony_enabled: bool = False
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""
    twilio_webhook_base_url: str = ""
    twilio_transfer_number: str = ""
    demo_call_allowed_numbers: str = ""
    operator_token: str = ""
    validate_twilio_signatures: bool = True

    # Voice runtime
    audio_sample_rate: int = 16000
    barge_in_min_speech_ms: int = 250
    max_ws_message_bytes: int = 64 * 1024
    max_text_turn_chars: int = 500
    max_concurrent_sessions: int = 25
    session_idle_timeout_s: float = 300.0

    # Rate limiting (per client IP). X-Forwarded-For (right-most entry) is trusted only when the
    # backend is reachable solely through a proxy that appends it (Railway, Vercel).
    trust_proxy_headers: bool = True
    rate_limit_sessions_per_minute: int = 10
    rate_limit_calls_per_hour: int = 5

    # Demo policy (simulated; NOT legal requirements)
    # POLICY_COUNTRY=JP applies the simulated calling window below to outbound contact.
    # Empty (POLICY_COUNTRY=) or any other country -> window NOT_APPLICABLE. Read from the raw
    # environment so an explicitly empty value is honoured (env_ignore_empty would drop it).
    policy_country: str = Field(default_factory=lambda: os.environ.get("POLICY_COUNTRY", "JP"))
    policy_timezone: str = "Asia/Tokyo"
    policy_calling_start_hour: int = 8
    policy_calling_end_hour: int = 21
    policy_max_contact_attempts: int = 3
    policy_max_identity_attempts: int = 2

    @field_validator("database_url")
    @classmethod
    def _normalise_db_url(cls, v: str) -> str:
        # Railway/Heroku style URLs -> async driver.
        if v.startswith("postgres://"):
            v = "postgresql://" + v[len("postgres://") :]
        if v.startswith("postgresql://"):
            v = "postgresql+asyncpg://" + v[len("postgresql://") :]
        if v.startswith("postgresql+asyncpg://") and "sslmode=" in v:
            # libpq's sslmode is not an asyncpg argument; asyncpg takes ssl=<mode>.
            v = re.sub(r"([?&])sslmode=", r"\1ssl=", v)
        return v

    @property
    def database_backend(self) -> str:
        """'postgresql' | 'sqlite' | 'other' — safe to expose (no host, no credentials)."""
        if self.database_url.startswith("postgresql"):
            return "postgresql"
        if self.database_url.startswith("sqlite"):
            return "sqlite"
        return "other"

    @property
    def database_durable(self) -> bool:
        """Survives a container restart/redeploy. A SQLite file lives inside the container."""
        return self.database_backend == "postgresql"

    # ---- derived capability flags -------------------------------------------------
    @property
    def cloudflare_configured(self) -> bool:
        return bool(self.cloudflare_account_id and self.cloudflare_api_token)

    @property
    def cartesia_configured(self) -> bool:
        return bool(self.cartesia_api_key)

    @property
    def twilio_configured(self) -> bool:
        return bool(
            self.twilio_account_sid
            and self.twilio_auth_token
            and self.twilio_phone_number
            and self.twilio_webhook_base_url
        )

    @property
    def telephony_active(self) -> bool:
        return self.telephony_enabled and self.twilio_configured

    @property
    def allowed_call_numbers(self) -> set[str]:
        return {n.strip() for n in self.demo_call_allowed_numbers.split(",") if n.strip()}

    @property
    def cors_origins(self) -> list[str]:
        origins = [self.frontend_url.rstrip("/")]
        origins += [o.strip().rstrip("/") for o in self.cors_extra_origins.split(",") if o.strip()]
        return origins

    def effective_llm_provider(self) -> str:
        return "cloudflare" if self.llm_provider == "cloudflare" and self.cloudflare_configured else "mock"

    def effective_stt_provider(self) -> str:
        return "cartesia" if self.stt_provider == "cartesia" and self.cartesia_configured else "mock"

    def effective_tts_provider(self) -> str:
        if self.tts_provider == "cartesia" and self.cartesia_configured and self.cartesia_voice_id:
            return "cartesia"
        return "mock"

    def effective_judge_provider(self) -> str:
        return "cloudflare" if self.judge_provider == "cloudflare" and self.cloudflare_configured else "mock"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def settings_for_tests(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "app_env": "test",
        "database_url": "sqlite+aiosqlite:///:memory:",
        "llm_provider": "mock",
        "stt_provider": "mock",
        "tts_provider": "mock",
        "telephony_enabled": False,
        "policy_country": "JP",
    }
    base.update(overrides)
    # Tests never read developer .env files (which may hold real credentials).
    return Settings(_env_file=None, **base)  # type: ignore[arg-type,call-arg]


__all__ = ["Settings", "get_settings", "settings_for_tests"]
