"""Tests for LLM clients, mock enforcement (Rule 4), and client factory."""

from __future__ import annotations

import pytest

from exitkit.ledger.models import ProposalKind
from exitkit.llm.client import (
    GoogleGenAIClient,
    MockModelClient,
    ModelError,
    OllamaClient,
    get_model_client,
)


def test_mock_client_rule_4_stamping_appdirs() -> None:
    """Mock client must stamp every output with MOCK and set is_mock=True (Rule 4)."""
    client = MockModelClient()
    proposal, payload_bytes, model_info = client.generate_proposal(
        target_dep="appdirs",
        prompt="Dummy prompt",
    )

    # Invariants for Rule 4
    assert model_info.is_mock is True
    assert model_info.backend == "mock"
    assert "mock" in model_info.model_id.lower()
    assert "[MOCK]" in proposal.rationale

    # Specific proposal values
    assert proposal.target_dependency == "appdirs"
    assert proposal.proposed_package == "platformdirs"
    assert proposal.proposed_specifier == ">=4.0.0"
    assert proposal.kind == ProposalKind.REPLACE
    assert proposal.confidence >= 0.9
    assert len(proposal.migration_steps) >= 2
    assert payload_bytes is not None


def test_mock_client_rule_4_stamping_jinja2() -> None:
    """Jinja2 proposal must propose UPGRADE within Jinja2 (upgrade-first strategy)."""
    client = MockModelClient(model_id="gemma-custom")
    assert client.model_id.startswith("mock-")

    proposal, _, model_info = client.generate_proposal(
        target_dep="jinja2",
        prompt="Dummy prompt",
    )

    assert model_info.is_mock is True
    assert proposal.kind == ProposalKind.UPGRADE
    assert proposal.proposed_package.lower() == "jinja2"
    assert ">=3" in proposal.proposed_specifier
    assert "[MOCK]" in proposal.rationale


def test_mock_client_rule_4_stamping_toml() -> None:
    """toml proposal must propose modern replacement tomli."""
    client = MockModelClient()
    proposal, _, model_info = client.generate_proposal(
        target_dep="toml",
        prompt="Dummy prompt",
    )

    assert model_info.is_mock is True
    assert proposal.kind == ProposalKind.REPLACE
    assert proposal.proposed_package == "tomli"


def test_mock_client_generic_fallback() -> None:
    """Other packages receive deterministic generic upgrade proposal with [MOCK]."""
    client = MockModelClient()
    proposal, _, model_info = client.generate_proposal(
        target_dep="some-rare-dep",
        prompt="Dummy prompt",
    )

    assert model_info.is_mock is True
    assert proposal.kind == ProposalKind.UPGRADE
    assert proposal.proposed_package == "some-rare-dep"
    assert "[MOCK]" in proposal.rationale


def test_get_model_client_mock() -> None:
    """Factory creates MockModelClient when requested."""
    client = get_model_client(backend="mock")
    assert isinstance(client, MockModelClient)
    assert client.backend == "mock"


def test_get_model_client_missing_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Factory raises ModelError with actionable advice when env vars are absent."""
    monkeypatch.delenv("EXITKIT_GEMMA_MODEL", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(ModelError) as exc_info:
        get_model_client()
    assert "EXITKIT_GEMMA_MODEL" in str(exc_info.value)

    with pytest.raises(ModelError) as exc_info:
        get_model_client(backend="google")
    assert "EXITKIT_GEMMA_MODEL is not set" in str(exc_info.value)

    with pytest.raises(ModelError) as exc_info:
        get_model_client(backend="ollama")
    assert "EXITKIT_GEMMA_MODEL is not set" in str(exc_info.value)


def test_google_client_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """GoogleGenAIClient raises ModelError if API key is missing."""
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(ModelError) as exc:
        GoogleGenAIClient(model_id="gemma-4-it")
    assert "Google GenAI API key is missing" in str(exc.value)


def test_ollama_client_initialization() -> None:
    """OllamaClient initializes with host and model."""
    client = OllamaClient(model_id="gemma:7b", host="http://my-host:11434/")
    assert client.host == "http://my-host:11434"
    assert client.model_id == "gemma:7b"
