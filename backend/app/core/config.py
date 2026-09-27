"""Application configuration.

All secrets and environment-specific values are read from environment variables
(or a local ``.env`` file). Nothing sensitive is hardcoded here.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BACKEND_ROOT / ".env", BACKEND_ROOT.parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "SpendLens AI"
    environment: str = Field(default="development", description="development | test | production")
    log_level: str = "INFO"

    # --- Database -----------------------------------------------------------
    database_url: str = "postgresql+psycopg://spendlens:spendlens@localhost:5446/spendlens"

    # --- Storage ------------------------------------------------------------
    storage_dir: Path = BACKEND_ROOT / "var" / "uploads"
    max_upload_mb: int = 20
    max_pdf_pages: int = 60
    max_image_pixels: int = 40_000_000
    default_retention_days: int = 7
    allowed_retention_days: tuple[int, ...] = (1, 7, 30, 90)

    # --- Sessions -----------------------------------------------------------
    session_cookie_name: str = "spendlens_session"
    session_cookie_secure: bool = False
    session_ttl_days: int = 90

    # --- Groq (vision + reasoning) -------------------------------------------
    groq_api_key: SecretStr | None = None
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_vision_model: str = "meta-llama/llama-4-scout-17b-16e-instruct"
    groq_text_model: str = "llama-3.3-70b-versatile"
    groq_timeout_seconds: float = 90.0
    vision_max_pages: int = 20
    vision_render_dpi: int = 150

    # --- Document extraction ------------------------------------------------
    docling_enabled: bool = True
    docling_table_structure: bool = True
    # A page with fewer extracted characters than this is treated as scanned/image-only.
    scanned_page_char_threshold: int = 40

    # --- Worker -------------------------------------------------------------
    embedded_worker: bool = True
    worker_poll_seconds: float = 1.0
    job_stale_after_seconds: int = 900
    job_max_attempts: int = 3
    retention_sweep_seconds: int = 300

    # --- HTTP ---------------------------------------------------------------
    cors_origins: list[str] = ["http://localhost:3020"]

    @field_validator("groq_api_key", mode="before")
    @classmethod
    def _blank_key_is_none(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def groq_enabled(self) -> bool:
        return self.groq_api_key is not None


@lru_cache
def get_settings() -> Settings:
    return Settings()
