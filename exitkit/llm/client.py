"""LLM client implementations supporting Google GenAI, Ollama, and Mock backends."""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any, Literal

import httpx

from exitkit.ledger.models import ModelInfo, ProposalKind
from exitkit.llm.models import MigrationProposal
from exitkit.llm.prompts import PROPOSAL_JSON_SCHEMA, SYSTEM_PROMPT


class ModelError(RuntimeError):
    """Exception raised when an LLM model invocation fails."""


class BaseModelClient(ABC):
    """Abstract base class for LLM backends."""

    def __init__(self, model_id: str, backend: Literal["google", "ollama", "mock"]) -> None:
        self.model_id = model_id
        self.backend = backend

    @abstractmethod
    def generate_proposal(
        self,
        target_dep: str,
        prompt: str,
    ) -> tuple[MigrationProposal, bytes, ModelInfo]:
        """Generate a structured migration proposal.

        Returns:
            tuple of (MigrationProposal, raw_response_bytes, ModelInfo)
        """

    @abstractmethod
    def generate_gap_triage(
        self,
        gap_id: str,
        category: str,
        description: str,
        failing_context: str | None = None,
    ) -> tuple[str, str, ModelInfo]:
        """Generate root cause and suggested fix triage for an observed gap.

        Returns:
            tuple of (cause, suggested_fix, ModelInfo)
        """


class GoogleGenAIClient(BaseModelClient):
    """Client for Google GenAI / Gemini API supporting bounded JSON schema generation."""

    def __init__(
        self,
        model_id: str,
        api_key: str | None = None,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        super().__init__(model_id=model_id, backend="google")
        self.api_key = (
            api_key or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        )
        if not self.api_key:
            raise ModelError(
                "Google GenAI API key is missing. Set GOOGLE_API_KEY or GEMINI_API_KEY."
            )
        self.timeout = timeout
        self.transport = transport

    def generate_proposal(
        self,
        target_dep: str,
        prompt: str,
    ) -> tuple[MigrationProposal, bytes, ModelInfo]:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_id}:generateContent"
            f"?key={self.api_key}"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": PROPOSAL_JSON_SCHEMA,
                "temperature": 0.1,
            },
        }

        client_kwargs: dict[str, Any] = {"timeout": self.timeout}
        if self.transport:
            client_kwargs["transport"] = self.transport

        with httpx.Client(**client_kwargs) as client:
            resp = client.post(url, json=payload)
            if resp.status_code != 200:
                raise ModelError(f"Google GenAI API error ({resp.status_code}): {resp.text}")

            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                raise ModelError("Google GenAI returned no response candidates.")

            text_content = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "{}")
            raw_proposal = json.loads(text_content)
            proposal = MigrationProposal.model_validate(raw_proposal)

            model_info = ModelInfo(
                model_id=self.model_id,
                backend="google",
                access_method="https://generativelanguage.googleapis.com/v1beta",
                timestamp=datetime.now(UTC),
                is_mock=False,
            )
            return proposal, resp.content, model_info

    def generate_gap_triage(
        self,
        gap_id: str,
        category: str,
        description: str,
        failing_context: str | None = None,
    ) -> tuple[str, str, ModelInfo]:
        prompt = (
            f"Analyze this compatibility gap observed during a Python dependency migration:\n"
            f"Gap ID: {gap_id}\n"
            f"Category: {category}\n"
            f"Description: {description}\n"
            f"Context / Error:\n{failing_context or 'N/A'}\n\n"
            f"Identify the precise root cause and provide a minimal suggested code fix or shim."
        )
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_id}:generateContent"
            f"?key={self.api_key}"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "systemInstruction": {
                "parts": [
                    {
                        "text": (
                            "You are Gemma 4, an expert Python dependency migration assistant. "
                            "Output only valid JSON with keys 'cause' (string) and "
                            "'suggested_fix' (string)."
                        )
                    }
                ]
            },
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.1,
            },
        }

        client_kwargs: dict[str, Any] = {"timeout": self.timeout}
        if self.transport:
            client_kwargs["transport"] = self.transport

        with httpx.Client(**client_kwargs) as client:
            resp = client.post(url, json=payload)
            if resp.status_code != 200:
                raise ModelError(f"Google GenAI API error ({resp.status_code}): {resp.text}")

            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                raise ModelError("Google GenAI returned no response candidates.")

            text_content = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "{}")
            triage_data = json.loads(text_content)
            cause = str(triage_data.get("cause", "Unknown root cause"))
            suggested_fix = str(triage_data.get("suggested_fix", "No fix suggested"))

            model_info = ModelInfo(
                model_id=self.model_id,
                backend="google",
                access_method="https://generativelanguage.googleapis.com/v1beta",
                timestamp=datetime.now(UTC),
                is_mock=False,
            )
            return cause, suggested_fix, model_info


class OllamaClient(BaseModelClient):
    """Client for local Ollama server supporting structured JSON outputs."""

    def __init__(
        self,
        model_id: str,
        host: str | None = None,
        timeout: float = 120.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        super().__init__(model_id=model_id, backend="ollama")
        self.host = (host or os.environ.get("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")
        self.timeout = timeout
        self.transport = transport

    def generate_proposal(
        self,
        target_dep: str,
        prompt: str,
    ) -> tuple[MigrationProposal, bytes, ModelInfo]:
        url = f"{self.host}/api/chat"
        payload = {
            "model": self.model_id,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.1},
        }

        client_kwargs: dict[str, Any] = {"timeout": self.timeout}
        if self.transport:
            client_kwargs["transport"] = self.transport

        with httpx.Client(**client_kwargs) as client:
            resp = client.post(url, json=payload)
            if resp.status_code != 200:
                raise ModelError(f"Ollama API error ({resp.status_code}): {resp.text}")

            data = resp.json()
            text_content = data.get("message", {}).get("content", "{}")
            raw_proposal = json.loads(text_content)
            proposal = MigrationProposal.model_validate(raw_proposal)

            model_info = ModelInfo(
                model_id=self.model_id,
                backend="ollama",
                access_method=self.host,
                timestamp=datetime.now(UTC),
                is_mock=False,
            )
            return proposal, resp.content, model_info

    def generate_gap_triage(
        self,
        gap_id: str,
        category: str,
        description: str,
        failing_context: str | None = None,
    ) -> tuple[str, str, ModelInfo]:
        prompt = (
            f"Analyze this compatibility gap observed during a Python dependency migration:\n"
            f"Gap ID: {gap_id}\n"
            f"Category: {category}\n"
            f"Description: {description}\n"
            f"Context / Error:\n{failing_context or 'N/A'}\n\n"
            f"Identify the precise root cause and provide a minimal suggested code fix or "
            f"adapter shim. Output JSON with keys 'cause' and 'suggested_fix'."
        )
        url = f"{self.host}/api/chat"
        payload = {
            "model": self.model_id,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are Gemma 4, an expert Python migration assistant. "
                        "Output only valid JSON with keys 'cause' and 'suggested_fix'."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.1},
        }

        client_kwargs: dict[str, Any] = {"timeout": self.timeout}
        if self.transport:
            client_kwargs["transport"] = self.transport

        with httpx.Client(**client_kwargs) as client:
            resp = client.post(url, json=payload)
            if resp.status_code != 200:
                raise ModelError(f"Ollama API error ({resp.status_code}): {resp.text}")

            data = resp.json()
            text_content = data.get("message", {}).get("content", "{}")
            triage_data = json.loads(text_content)
            cause = str(triage_data.get("cause", "Unknown root cause"))
            suggested_fix = str(triage_data.get("suggested_fix", "No fix suggested"))

            model_info = ModelInfo(
                model_id=self.model_id,
                backend="ollama",
                access_method=self.host,
                timestamp=datetime.now(UTC),
                is_mock=False,
            )
            return cause, suggested_fix, model_info


class MockModelClient(BaseModelClient):
    """Deterministic mock proposer for unit testing stamped strictly with 'MOCK' (Rule 4)."""

    def __init__(self, model_id: str = "mock-gemma-4") -> None:
        # Enforce Rule 4: Must stamp every output MOCK
        clean_id = model_id if "mock" in model_id.lower() else f"mock-{model_id}"
        super().__init__(model_id=clean_id, backend="mock")

    def generate_proposal(
        self,
        target_dep: str,
        prompt: str,
    ) -> tuple[MigrationProposal, bytes, ModelInfo]:
        cname = target_dep.lower()

        if cname in ("appdirs",):
            proposal = MigrationProposal(
                target_dependency=target_dep,
                proposed_package="platformdirs",
                proposed_specifier=">=4.0.0",
                kind=ProposalKind.REPLACE,
                rationale=(
                    "[MOCK] appdirs is unmaintained (last release over 7 years ago). "
                    "platformdirs is the official community maintained fork and drop-in."
                ),
                migration_steps=[
                    "Update manifest dependencies: replace 'appdirs' with 'platformdirs>=4.0.0'",
                    "In source files, replace 'from appdirs' with 'from platformdirs'",
                    "Verify user_cache_dir and user_data_dir return equivalent paths.",
                ],
                confidence=0.95,
                expected_shim_needed=False,
                shim_code=None,
            )
        elif cname in ("jinja2",):
            proposal = MigrationProposal(
                target_dependency=target_dep,
                proposed_package="Jinja2",
                proposed_specifier=">=3.0.0",
                kind=ProposalKind.UPGRADE,
                rationale=(
                    "[MOCK] Jinja2 2.11.3 fails on Python 3.12+ due to soft_unicode removal. "
                    "Upgrading within Jinja2 to >=3.0.0 resolves Python 3.12/3.13 compatibility."
                ),
                migration_steps=[
                    "Update manifest dependencies: change 'Jinja2==2.11.3' to 'Jinja2>=3.0.0'",
                    "Verify Environment and FileSystemLoader instantiation in reporting modules.",
                ],
                confidence=0.98,
                expected_shim_needed=False,
                shim_code=None,
            )
        elif cname in ("toml",):
            proposal = MigrationProposal(
                target_dependency=target_dep,
                proposed_package="tomli",
                proposed_specifier=">=2.0.1",
                kind=ProposalKind.REPLACE,
                rationale=(
                    "[MOCK] toml package is inactive and has known parser issues. "
                    "For Python >=3.12, tomllib is in standard library, or tomli provides compat."
                ),
                migration_steps=[
                    "Replace 'import toml' with 'import tomllib' or 'import tomli as toml'",
                    "Change 'toml.load(f)' to 'tomllib.load(f)' with binary file reading.",
                ],
                confidence=0.92,
                expected_shim_needed=False,
                shim_code=None,
            )
        else:
            proposal = MigrationProposal(
                target_dependency=target_dep,
                proposed_package=target_dep,
                proposed_specifier=">=latest",
                kind=ProposalKind.UPGRADE,
                rationale=f"[MOCK] Generic upgrade proposal for {target_dep}.",
                migration_steps=[
                    f"Update {target_dep} constraint to latest stable release.",
                    "Run regression test suite.",
                ],
                confidence=0.85,
                expected_shim_needed=False,
                shim_code=None,
            )

        payload_bytes = json.dumps(proposal.model_dump(mode="json"), indent=2).encode("utf-8")

        model_info = ModelInfo(
            model_id=self.model_id,
            backend="mock",
            access_method="in-memory-mock",
            timestamp=datetime.now(UTC),
            is_mock=True,
        )
        return proposal, payload_bytes, model_info

    def generate_gap_triage(
        self,
        gap_id: str,
        category: str,
        description: str,
        failing_context: str | None = None,
    ) -> tuple[str, str, ModelInfo]:
        desc_lower = (description + " " + (failing_context or "")).lower()
        if "soft_unicode" in desc_lower or "jinja2" in desc_lower:
            cause = (
                "[MOCK] Jinja2 2.11.3 attempts to import soft_unicode from markupsafe, "
                "which was removed in MarkupSafe >= 2.1.0."
            )
            suggested_fix = "[MOCK] Upgrade Jinja2 to >=3.0.0 or pin markupsafe<2.1.0."
        elif any(k in desc_lower for k in ("platformdirs", "appdirs", "user_data_dir")):
            cause = (
                "[MOCK] Parameter signature or return type difference between "
                "appdirs and platformdirs."
            )
            suggested_fix = (
                "[MOCK] Pass explicit appname and appauthor arguments or wrap in an adapter shim."
            )
        else:
            cause = f"[MOCK] Root cause for {gap_id}: {description}"
            suggested_fix = "[MOCK] Adapt invocation site or provide a backward-compatible shim."

        model_info = ModelInfo(
            model_id=self.model_id,
            backend="mock",
            access_method="in-memory-mock",
            timestamp=datetime.now(UTC),
            is_mock=True,
        )
        return cause, suggested_fix, model_info


def get_model_client(
    backend: Literal["google", "ollama", "mock"] | None = None,
    model_id: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> BaseModelClient:
    """Factory creating an LLM client based on requested or environment configuration."""
    # Determine model ID
    effective_model = model_id or os.environ.get("EXITKIT_GEMMA_MODEL")

    # Determine backend
    if backend == "mock":
        return MockModelClient(model_id=effective_model or "mock-gemma-4")

    if backend == "ollama":
        if not effective_model:
            raise ModelError("EXITKIT_GEMMA_MODEL is not set. Specify model (e.g. gemma4:latest).")
        return OllamaClient(model_id=effective_model, transport=transport)

    if backend == "google":
        if not effective_model:
            raise ModelError("EXITKIT_GEMMA_MODEL is not set. Specify model (e.g. gemma-4-it).")
        return GoogleGenAIClient(model_id=effective_model, transport=transport)

    # Automatic detection: check if Google API key is configured
    if os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"):
        if not effective_model:
            raise ModelError(
                "EXITKIT_GEMMA_MODEL environment variable is not set.\n"
                "To use Google GenAI: export EXITKIT_GEMMA_MODEL=gemma-4-it (or gemini-1.5-flash)\n"
                "To use Ollama: export EXITKIT_GEMMA_MODEL=gemma:7b and pass --backend ollama\n"
                "For test runs without API keys: pass --backend mock"
            )
        return GoogleGenAIClient(model_id=effective_model, transport=transport)

    # If neither API key is set, and backend was not specified
    if not effective_model:
        raise ModelError(
            "Neither EXITKIT_GEMMA_MODEL nor API keys are set.\n"
            "Configure your environment:\n"
            "  1. Export model ID: export EXITKIT_GEMMA_MODEL=gemma-4-it\n"
            "  2. Export API key:  export GOOGLE_API_KEY=your_key\n"
            "Or run locally with Ollama:\n"
            "  export EXITKIT_GEMMA_MODEL=gemma:7b --backend ollama\n"
            "Or for offline unit testing:\n"
            "  use '--backend mock'"
        )

    # If model is set, default to Google
    return GoogleGenAIClient(model_id=effective_model, transport=transport)
