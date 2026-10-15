"""Tests for live LLM client network transports using httpx.MockTransport."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from exitkit.ledger.models import ProposalKind
from exitkit.llm.client import GoogleGenAIClient, ModelError, OllamaClient


def test_google_client_successful_response() -> None:
    """GoogleGenAIClient parses candidate JSON schema and produces MigrationProposal."""
    proposal_data = {
        "target_dependency": "jinja2",
        "proposed_package": "Jinja2",
        "proposed_specifier": ">=3.1.2",
        "kind": "UPGRADE",
        "rationale": "Jinja2 3.1.2 resolves Python 3.12 compatibility.",
        "migration_steps": ["Update requirements.txt: Jinja2>=3.1.2"],
        "confidence": 0.99,
        "expected_shim_needed": False,
        "shim_code": None,
    }

    mock_gemini_response = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": json.dumps(proposal_data)}],
                    "role": "model",
                },
                "finishReason": "STOP",
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert "generativelanguage.googleapis.com" in str(request.url)
        assert request.url.params["key"] == "test-api-key"
        body = json.loads(request.content.decode("utf-8"))
        assert "contents" in body
        assert "generationConfig" in body
        return httpx.Response(200, json=mock_gemini_response)

    transport = httpx.MockTransport(handler)
    client = GoogleGenAIClient(
        model_id="gemma-4-it",
        api_key="test-api-key",
        transport=transport,
    )

    proposal, raw_bytes, model_info = client.generate_proposal(
        target_dep="jinja2",
        prompt="Test prompt",
    )

    assert proposal.target_dependency == "jinja2"
    assert proposal.kind == ProposalKind.UPGRADE
    assert proposal.proposed_package == "Jinja2"
    assert proposal.confidence == 0.99
    assert model_info.is_mock is False
    assert model_info.backend == "google"
    assert model_info.model_id == "gemma-4-it"
    assert b"candidates" in raw_bytes


def test_google_client_http_error() -> None:
    """GoogleGenAIClient raises ModelError on HTTP error status."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="API key not valid. Please pass a valid API key.")

    transport = httpx.MockTransport(handler)
    client = GoogleGenAIClient(
        model_id="gemma-4-it",
        api_key="bad-key",
        transport=transport,
    )

    with pytest.raises(ModelError) as exc:
        client.generate_proposal(target_dep="appdirs", prompt="Prompt")
    assert "403" in str(exc.value)


def test_ollama_client_successful_response() -> None:
    """OllamaClient parses chat message JSON and produces MigrationProposal."""
    proposal_data: dict[str, Any] = {
        "target_dependency": "appdirs",
        "proposed_package": "platformdirs",
        "proposed_specifier": ">=4.0.0",
        "kind": "REPLACE",
        "rationale": "appdirs is deprecated. platformdirs is the drop-in replacement.",
        "migration_steps": ["Replace appdirs with platformdirs"],
        "confidence": 0.94,
        "expected_shim_needed": False,
        "shim_code": None,
    }

    mock_ollama_response = {
        "model": "gemma4:latest",
        "message": {
            "role": "assistant",
            "content": json.dumps(proposal_data),
        },
        "done": True,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://localhost:11434/api/chat"
        body = json.loads(request.content.decode("utf-8"))
        assert body["model"] == "gemma4:latest"
        assert body["format"] == "json"
        return httpx.Response(200, json=mock_ollama_response)

    transport = httpx.MockTransport(handler)
    client = OllamaClient(
        model_id="gemma4:latest",
        transport=transport,
    )

    proposal, raw_bytes, model_info = client.generate_proposal(
        target_dep="appdirs",
        prompt="Test prompt",
    )

    assert proposal.target_dependency == "appdirs"
    assert proposal.kind == ProposalKind.REPLACE
    assert proposal.proposed_package == "platformdirs"
    assert model_info.is_mock is False
    assert model_info.backend == "ollama"
    assert b"gemma4:latest" in raw_bytes


def test_ollama_client_http_error() -> None:
    """OllamaClient raises ModelError on server error."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error: model not found")

    transport = httpx.MockTransport(handler)
    client = OllamaClient(
        model_id="nonexistent-model",
        transport=transport,
    )

    with pytest.raises(ModelError) as exc:
        client.generate_proposal(target_dep="jinja2", prompt="Prompt")
    assert "500" in str(exc.value)
