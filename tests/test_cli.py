"""Tests for ExitKit CLI and doctor command."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from exitkit import __version__
from exitkit.cli import app
from exitkit.config import Settings, check_env_status

runner = CliRunner()


def test_cli_version() -> None:
    """Verify exitkit --version displays the version and exits cleanly."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output
    assert "ExitKit" in result.output


def test_cli_doctor_runs() -> None:
    """Verify exitkit doctor executes and reports core components."""
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "Python Version" in result.output
    assert "uv Package Manager" in result.output
    assert "Git Binary" in result.output
    assert "Google GenAI / Gemini Key" in result.output
    assert "Gemma 4 Model ID" in result.output
    assert "GitHub Token" in result.output


def test_cli_doctor_never_leaks_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify doctor never displays secret key values even when present in env."""
    secret_key = "super_secret_api_key_value_that_must_never_leak"
    secret_token = "ghp_super_secret_github_token_value"
    monkeypatch.setenv("GOOGLE_API_KEY", secret_key)
    monkeypatch.setenv("GEMINI_API_KEY", secret_key)
    monkeypatch.setenv("GITHUB_TOKEN", secret_token)
    monkeypatch.setenv("EXITKIT_GEMMA_MODEL", "gemma-4-it")

    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert secret_key not in result.output
    assert secret_token not in result.output
    assert "GOOGLE_API_KEY detected" in result.output
    assert "GEMINI_API_KEY detected" in result.output
    assert "gemma-4-it" in result.output


def test_config_env_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify check_env_status accurately reflects environment variables."""
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("EXITKIT_GEMMA_MODEL", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("EXITKIT_LLM_BACKEND", "ollama")

    status = check_env_status()
    assert not status.google_api_key_set
    assert not status.gemini_api_key_set
    assert not status.has_genai_key
    assert not status.gemma_model_set
    assert not status.github_token_set
    assert status.llm_backend == "ollama"

    monkeypatch.setenv("GOOGLE_API_KEY", "dummy_key")
    monkeypatch.setenv("EXITKIT_GEMMA_MODEL", "gemma-4-27b")
    status2 = check_env_status()
    assert status2.google_api_key_set
    assert status2.has_genai_key
    assert status2.gemma_model_set
    assert status2.gemma_model_name == "gemma-4-27b"


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify Settings.from_env loads configured values correctly."""
    monkeypatch.setenv("EXITKIT_GEMMA_MODEL", "gemma4:test")
    monkeypatch.setenv("EXITKIT_LLM_BACKEND", "mock")
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:11434")

    settings = Settings.from_env()
    assert settings.gemma_model == "gemma4:test"
    assert settings.llm_backend == "mock"
    assert settings.ollama_host == "http://127.0.0.1:11434"
