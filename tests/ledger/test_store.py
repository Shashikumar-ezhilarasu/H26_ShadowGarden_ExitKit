"""Tests for EvidenceStore content addressing, deduplication, and tamper detection."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from exitkit.ledger.integrity import verify_capsule_evidence
from exitkit.ledger.models import (
    Capsule,
    Claim,
    ClaimSource,
    DependencyInfo,
    EvidenceKind,
    TrustLevel,
)
from exitkit.ledger.store import EvidenceStore


def test_evidence_store_sha256_matches_content(tmp_path: Path) -> None:
    """Store accurately calculates and records SHA-256 for saved bytes."""
    store = EvidenceStore(tmp_path)
    payload = b"Sample execution log output: 10 passed, 0 failed\n"
    expected_hash = hashlib.sha256(payload).hexdigest()

    ev = store.add_bytes(
        kind=EvidenceKind.COMMAND_OUTPUT,
        content=payload,
        description="Command execution log",
    )

    assert ev.sha256 == expected_hash
    assert ev.size_bytes == len(payload)
    assert store.get_content_bytes(ev) == payload

    result = store.verify(ev)
    assert result.valid
    assert len(result.problems) == 0


def test_evidence_store_deduplicates_identical_content(tmp_path: Path) -> None:
    """Adding identical content produces an identical filename and reuses storage."""
    store = EvidenceStore(tmp_path)
    data = {"package": "jinja2", "status": "broken"}

    ev1 = store.add_json(
        kind=EvidenceKind.HTTP_RESPONSE,
        data=data,
        description="First fetch",
        evidence_id="ev-pypi-1",
    )
    ev2 = store.add_json(
        kind=EvidenceKind.HTTP_RESPONSE,
        data=data,
        description="Second identical fetch",
        evidence_id="ev-pypi-2",
    )

    assert ev1.sha256 == ev2.sha256
    assert ev1.path == ev2.path

    # Only one physical file should exist in the evidence directory
    files = list(store.evidence_dir.glob("*.json"))
    assert len(files) == 1


def test_tamper_detection_flipped_byte_caught_by_store_verify(tmp_path: Path) -> None:
    """Explicit Tamper Test: Flipping a byte in an evidence file is caught by store.verify()."""
    store = EvidenceStore(tmp_path)
    original_text = "Baseline test run: PASS_AS_DECLARED"
    ev = store.add_text(
        kind=EvidenceKind.TEST_RUN,
        text=original_text,
        description="Original baseline run receipt",
    )

    # Confirm valid initially
    assert store.verify(ev).valid

    # Tamper with file: flip one character
    file_path = store.evidence_dir / ev.path
    os.chmod(file_path, 0o644)  # Ensure writeable for simulation
    tampered_bytes = bytearray(file_path.read_bytes())
    tampered_bytes[0] = ord("X")  # Flip first character 'B' -> 'X'
    file_path.write_bytes(tampered_bytes)

    # Verification must catch the tampering
    result = store.verify(ev)
    assert not result.valid
    assert len(result.problems) == 1
    assert result.problems[0].problem_type == "HASH_MISMATCH"
    assert ev.sha256 in result.problems[0].message


def test_tamper_detection_flipped_byte_caught_by_capsule_integrity(tmp_path: Path) -> None:
    """Explicit Tamper Test: Flipping a byte is caught by verify_capsule_evidence()."""
    store = EvidenceStore(tmp_path)
    ev = store.add_text(
        kind=EvidenceKind.COMMAND_OUTPUT,
        text="All tests passed successfully",
        description="Clean run receipt",
        evidence_id="ev-clean-1",
    )

    claim = Claim(
        id="claim-obs-tamper",
        statement="Observed clean test execution",
        trust_level=TrustLevel.OBSERVED,
        source=ClaimSource.TOOL,
        evidence_ids=[ev.id],
    )

    capsule = Capsule(
        dependency=DependencyInfo(name="clean-dep"),
        claims=[claim],
        evidence=[ev],
    )

    # Initial capsule integrity check is clean
    problems_before = verify_capsule_evidence(capsule, store.evidence_dir)
    assert len(problems_before) == 0

    # Tamper: flip last byte
    file_path = store.evidence_dir / ev.path
    os.chmod(file_path, 0o644)
    tampered_bytes = bytearray(file_path.read_bytes())
    tampered_bytes[-1] ^= 0xFF
    file_path.write_bytes(tampered_bytes)

    # Verify capsule detects tampering
    problems_after = verify_capsule_evidence(capsule, store.evidence_dir)
    assert len(problems_after) == 1
    assert problems_after[0].problem_type == "HASH_MISMATCH"


def test_evidence_store_rejects_path_traversal(tmp_path: Path) -> None:
    """Store prevents path traversal in custom extensions."""
    store = EvidenceStore(tmp_path)
    ev = store.add_bytes(
        kind=EvidenceKind.OTHER,
        content=b"content",
        description="Test ext sanitize",
        ext="../../evil.sh",
    )
    # The extension sanitizer converts traversal to safe 'bin'
    assert "/" not in ev.path
    assert ".." not in ev.path
    assert ev.path.endswith(".bin")
