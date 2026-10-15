"""Configuration and environment management for ExitKit."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class EnvStatus:
    """Status summary of environment variables required or optional for ExitKit."""

    google_api_key_set: bool
    gemini_api_key_set: bool
    gemma_model_set: bool
    github_token_set: bool
    gemma_model_name: str | None
    llm_backend: str

    @property
    def has_genai_key(self) -> bool:
        """Return True if at least one Google GenAI / Gemini key is available."""
        return self.google_api_key_set or self.gemini_api_key_set


@dataclass(frozen=True)
class Settings:
    """Runtime configuration for ExitKit operations."""

    gemma_model: str | None = None
    llm_backend: Literal["genai", "ollama", "mock"] = "genai"
    ollama_host: str = "http://localhost:11434"
    github_token: str | None = None
    google_api_key: str | None = None
    gemini_api_key: str | None = None

    @classmethod
    def from_env(cls) -> Settings:
        """Load settings from current process environment variables."""
        raw_backend = os.getenv("EXITKIT_LLM_BACKEND", "genai").strip().lower()
        backend: Literal["genai", "ollama", "mock"] = (
            raw_backend if raw_backend in ("genai", "ollama", "mock") else "genai"  # type: ignore[assignment]
        )
        return cls(
            gemma_model=os.getenv("EXITKIT_GEMMA_MODEL"),
            llm_backend=backend,
            ollama_host=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
            github_token=os.getenv("GITHUB_TOKEN"),
            google_api_key=os.getenv("GOOGLE_API_KEY"),
            gemini_api_key=os.getenv("GEMINI_API_KEY"),
        )


def check_env_status() -> EnvStatus:
    """Inspect environment variable availability without revealing secret values."""
    google_key = os.getenv("GOOGLE_API_KEY")
    gemini_key = os.getenv("GEMINI_API_KEY")
    gemma_model = os.getenv("EXITKIT_GEMMA_MODEL")
    github_token = os.getenv("GITHUB_TOKEN")
    backend = os.getenv("EXITKIT_LLM_BACKEND", "genai")

    return EnvStatus(
        google_api_key_set=bool(google_key and google_key.strip()),
        gemini_api_key_set=bool(gemini_key and gemini_key.strip()),
        gemma_model_set=bool(gemma_model and gemma_model.strip()),
        github_token_set=bool(github_token and github_token.strip()),
        gemma_model_name=gemma_model.strip() if gemma_model else None,
        llm_backend=backend,
    )
