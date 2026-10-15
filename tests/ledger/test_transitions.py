"""Tests for claim trust level transitions and transition restrictions."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from exitkit.ledger.models import (
    Capsule,
    Claim,
    ClaimSource,
    DependencyInfo,
    Evidence,
    EvidenceKind,
    ModelInfo,
    TrustLevel,
)
from exitkit.ledger.transitions import (
    LedgerError,
    check_transition_validity,
    refute_ai_claim,
    verify_ai_claim,
)


def sample_evidence(ev_id: str = "ev-trans-1") -> Evidence:
    return Evidence(
        id=ev_id,
        kind=EvidenceKind.TEST_RUN,
        path=f"{ev_id}.txt",
        sha256="c" * 64,
        size_bytes=120,
        created_at=datetime.now(UTC),
        description="Differential test run log",
    )


def sample_ai_claim(claim_id: str = "claim-ai-1") -> Claim:
    model_info = ModelInfo(
        model_id="gemma-4-it",
        backend="google",
        access_method="api",
        timestamp=datetime.now(UTC),
        is_mock=False,
    )
    return Claim(
        id=claim_id,
        statement="Replace appdirs with platformdirs",
        trust_level=TrustLevel.AI_PROPOSED,
        source=ClaimSource.MODEL,
        model_info=model_info,
    )


def test_verify_ai_claim_success() -> None:
    """verify_ai_claim creates a new immutable Claim in AI_PROPOSED_VERIFIED state."""
    original = sample_ai_claim("claim-original-1")
    verified = verify_ai_claim(
        original,
        evidence_ids=["ev-trans-1"],
        statement_note="All 10 unit tests pass",
        new_id="claim-ver-100",
    )

    assert verified.id == "claim-ver-100"
    assert verified.trust_level == TrustLevel.AI_PROPOSED_VERIFIED
    assert verified.source == ClaimSource.TOOL
    assert verified.supersedes == original.id
    assert verified.evidence_ids == ["ev-trans-1"]
    assert "All 10 unit tests pass" in (verified.note or "")

    # Assert immutability: original claim remains unchanged
    assert original.trust_level == TrustLevel.AI_PROPOSED
    assert original.supersedes is None


def test_refute_ai_claim_success() -> None:
    """refute_ai_claim creates a new immutable Claim in AI_PROPOSED_REFUTED state."""
    original = sample_ai_claim("claim-original-2")
    refuted = refute_ai_claim(
        original,
        evidence_ids=["ev-trans-1"],
        statement_note="AttributeError: 'module' has no attribute 'user_cache_dir'",
        new_id="claim-ref-200",
    )

    assert refuted.id == "claim-ref-200"
    assert refuted.trust_level == TrustLevel.AI_PROPOSED_REFUTED
    assert refuted.source == ClaimSource.TOOL
    assert refuted.supersedes == original.id
    assert refuted.evidence_ids == ["ev-trans-1"]
    assert "AttributeError" in (refuted.note or "")


def test_illegal_transitions_raise_ledger_error() -> None:
    """Illegal transitions must raise LedgerError."""
    # AI claim cannot be promoted to OBSERVED
    with pytest.raises(LedgerError, match="Illegal trust transition"):
        check_transition_validity(TrustLevel.AI_PROPOSED, TrustLevel.OBSERVED)

    # OBSERVED cannot transition to anything
    with pytest.raises(LedgerError, match="Illegal trust transition"):
        check_transition_validity(TrustLevel.OBSERVED, TrustLevel.AI_PROPOSED)

    with pytest.raises(LedgerError, match="Illegal trust transition"):
        check_transition_validity(TrustLevel.OBSERVED, TrustLevel.AI_PROPOSED_VERIFIED)

    # REFUTED cannot transition to VERIFIED
    with pytest.raises(LedgerError, match="Illegal trust transition"):
        check_transition_validity(TrustLevel.AI_PROPOSED_REFUTED, TrustLevel.AI_PROPOSED_VERIFIED)


def test_cannot_verify_non_ai_claim() -> None:
    """Attempting to verify an OBSERVED or already REFUTED claim raises LedgerError."""
    ev = sample_evidence()
    obs_claim = Claim(
        id="claim-obs-3",
        statement="Direct test output",
        trust_level=TrustLevel.OBSERVED,
        source=ClaimSource.TOOL,
        evidence_ids=[ev.id],
    )
    with pytest.raises(LedgerError, match="only AI_PROPOSED claims can transition"):
        verify_ai_claim(obs_claim, evidence_ids=[ev.id])

    original = sample_ai_claim("claim-ai-3")
    refuted = refute_ai_claim(original, evidence_ids=[ev.id])
    with pytest.raises(LedgerError, match="only AI_PROPOSED claims can transition"):
        verify_ai_claim(refuted, evidence_ids=[ev.id])


def test_capsule_preserves_both_original_and_transitioned_claim() -> None:
    """Capsule must retain both the original AI_PROPOSED claim and the resulting verdict."""
    ev = sample_evidence("ev-retention")
    original = sample_ai_claim("claim-ai-kept")
    refuted = refute_ai_claim(original, evidence_ids=[ev.id], new_id="claim-refuted-kept")

    capsule = Capsule(
        dependency=DependencyInfo(name="appdirs"),
        claims=[original, refuted],
        evidence=[ev],
    )

    claim_dict = {c.id: c for c in capsule.claims}
    assert "claim-ai-kept" in claim_dict
    assert "claim-refuted-kept" in claim_dict
    assert claim_dict["claim-refuted-kept"].supersedes == "claim-ai-kept"
