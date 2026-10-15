"""Data models for Dependency Exit Capsule bundling and verification."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class CapsuleCheckStatus(StrEnum):
    """Overall status of capsule verification."""

    VERIFIED = "VERIFIED"
    TAMPERED = "TAMPERED"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    TRUST_VIOLATION = "TRUST_VIOLATION"
    REPLAY_MISMATCH = "REPLAY_MISMATCH"


class CheckDetail(BaseModel):
    """Detailed record of an individual verification check."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    passed: bool
    message: str
    expected: str | None = None
    actual: str | None = None


class CapsuleVerificationResult(BaseModel):
    """Complete outcome of verifying a Dependency Exit Capsule."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    capsule_id: str
    capsule_path: str
    status: CapsuleCheckStatus
    is_valid: bool
    synthetic: bool
    checks: list[CheckDetail] = Field(default_factory=list)
    tampered_files: list[str] = Field(default_factory=list)
    replayed: bool = False
    replay_matched: bool | None = None
