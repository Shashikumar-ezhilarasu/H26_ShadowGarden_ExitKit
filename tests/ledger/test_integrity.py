"""Tests for capsule cryptographic validation context and canonical JSON serialization."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from exitkit.ledger.models import (
    Capsule,
    Claim,
    ClaimSource,
    DependencyInfo,
    EvidenceKind,
    TrustLevel,
)
from exitkit.ledger.store import EvidenceStore


def test_capsule_context_validation_rejects_tampered_evidence(tmp_path: Path) -> None:
    """Explicit Tamper Test: model_validate_json with context rejects tampered files."""
    store = EvidenceStore(tmp_path)
    ev = store.add_text(
        kind=EvidenceKind.TEST_RUN,
        text="Real verified test output",
        description="Run receipt",
        evidence_id="ev-int-1",
    )

    claim = Claim(
        id="claim-obs-int",
        statement="Tests ran and output recorded",
        trust_level=TrustLevel.OBSERVED,
        source=ClaimSource.TOOL,
        evidence_ids=[ev.id],
    )

    capsule = Capsule(
        dependency=DependencyInfo(name="test-dep"),
        claims=[claim],
        evidence=[ev],
    )

    capsule_json = capsule.model_dump_json()

    # Validating with correct evidence root passes
    validated = Capsule.model_validate_json(
        capsule_json,
        context={"evidence_root": str(store.evidence_dir)},
    )
    assert validated.capsule_id == capsule.capsule_id

    # Tamper with file on disk
    file_path = store.evidence_dir / ev.path
    os.chmod(file_path, 0o644)
    file_path.write_bytes(b"Tampered unauthorized content")

    # Re-validating with context must fail with hash mismatch
    with pytest.raises(ValidationError, match="Evidence hash mismatch"):
        Capsule.model_validate_json(
            capsule_json,
            context={"evidence_root": str(store.evidence_dir)},
        )

    # Validating without context still succeeds structurally
    without_ctx = Capsule.model_validate_json(capsule_json)
    assert without_ctx.capsule_id == capsule.capsule_id


def test_capsule_context_validation_rejects_missing_file(tmp_path: Path) -> None:
    """Capsule validation with context rejects missing evidence file."""
    store = EvidenceStore(tmp_path)
    ev = store.add_text(
        kind=EvidenceKind.COMMAND_OUTPUT,
        text="Some receipt",
        description="Command receipt",
        evidence_id="ev-missing-1",
    )

    claim = Claim(
        id="claim-obs-miss",
        statement="Command ran",
        trust_level=TrustLevel.OBSERVED,
        source=ClaimSource.TOOL,
        evidence_ids=[ev.id],
    )

    capsule = Capsule(
        dependency=DependencyInfo(name="test-dep"),
        claims=[claim],
        evidence=[ev],
    )

    capsule_json = capsule.model_dump_json()

    # Delete the evidence file
    file_path = store.evidence_dir / ev.path
    os.chmod(file_path, 0o644)
    file_path.unlink()

    with pytest.raises(ValidationError, match="Evidence file missing on disk"):
        Capsule.model_validate_json(
            capsule_json,
            context={"evidence_root": str(store.evidence_dir)},
        )


def test_canonical_json_roundtrip_deterministic() -> None:
    """Capsule.to_canonical_json() round-trip preserves byte-level equality and digest."""
    capsule = Capsule(
        dependency=DependencyInfo(name="linkwatch-dep"),
    )

    canonical_1 = capsule.to_canonical_json()
    digest_1 = capsule.capsule_digest()

    # Load back and re-serialize
    loaded = Capsule.model_validate_json(canonical_1)
    canonical_2 = loaded.to_canonical_json()
    digest_2 = loaded.capsule_digest()

    assert canonical_1 == canonical_2
    assert digest_1 == digest_2

    # Verify JSON keys are sorted
    parsed = json.loads(canonical_1)
    keys = list(parsed.keys())
    assert keys == sorted(keys)
