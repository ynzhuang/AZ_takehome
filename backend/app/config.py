"""Runtime configuration, read from environment variables (and an optional .env file).

API keys are never hard-coded. See .env.example at the repository root.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]

# A local .env (git-ignored) is optional; real environment variables take precedence.
load_dotenv(REPO_ROOT / ".env", override=False)


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    llm_provider: str  # "openai_compatible" or "stub"
    llm_base_url: str
    llm_api_key: str  # may be empty when a proxy injects credentials
    llm_model: str
    llm_timeout_seconds: float
    cors_origins: tuple[str, ...]

    @property
    def llm_label(self) -> str:
        if self.llm_provider == "stub":
            return "stub (keyword rules, no LLM)"
        return f"{self.llm_model} via {self.llm_base_url}"


def load_settings() -> Settings:
    provider = os.getenv("LLM_PROVIDER", "stub").strip().lower()
    if provider not in {"openai_compatible", "stub"}:
        raise ValueError(f"LLM_PROVIDER must be 'openai_compatible' or 'stub', got {provider!r}")
    model = os.getenv("LLM_MODEL", "").strip()
    if provider == "openai_compatible" and not model:
        raise ValueError("LLM_MODEL is required when LLM_PROVIDER=openai_compatible")
    return Settings(
        data_dir=Path(os.getenv("DATA_DIR", REPO_ROOT / "data")),
        llm_provider=provider,
        llm_base_url=os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/"),
        llm_api_key=os.getenv("LLM_API_KEY", "").strip(),
        llm_model=model,
        llm_timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "30")),
        cors_origins=tuple(
            o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()
        ),
    )
