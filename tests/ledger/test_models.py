"""Tests for ledger models, validation rules, and referential integrity."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from exitkit.ledger.models import (
    Capsule,
    Claim,
    ClaimSource,
    DependencyInfo,
    Evidence,
    EvidenceKind,
    ModelInfo,
    RiskSignal,
    SignalStatus,
    TrustLevel,
)


def make_dummy_evidence(ev_id: str = "ev-1") -> Evidence:
    return Evidence(
        id=ev_id,
        kind=EvidenceKind.COMMAND_OUTPUT,
        path=f"{ev_id}.txt",
        sha256="a" * 64,
        size_bytes=100,
        created_at=datetime.now(UTC),
        description="Sample command output evidence",
    )


def make_dummy_model_info(is_mock: bool = False) -> ModelInfo:
    return ModelInfo(
        model_id="gemma-4-it",
        backend="mock" if is_mock else "google",
        access_method="api",
        timestamp=datetime.now(UTC),
        is_mock=is_mock,
    )


def test_observed_claim_without_evidence_rejected() -> None:
    """OBSERVED claim without evidence must raise ValidationError."""
    with pytest.raises(ValidationError, match="requires at least one evidence_id"):
        Claim(
            id="claim-obs-1",
            statement="Jinja2==2.11.3 fails on modern markupsafe",
            trust_level=TrustLevel.OBSERVED,
            source=ClaimSource.TOOL,
            evidence_ids=[],
        )


def test_observed_claim_with_model_source_rejected() -> None:
    """OBSERVED claim originating from MODEL must raise ValidationError."""
    with pytest.raises(ValidationError, match="OBSERVED claim source must be TOOL"):
        Claim(
            id="claim-obs-2",
            statement="Jinja2 fails on modern markupsafe",
            trust_level=TrustLevel.OBSERVED,
            source=ClaimSource.MODEL,
            evidence_ids=["ev-1"],
        )


def test_ai_proposed_claim_without_model_info_rejected() -> None:
    """AI_PROPOSED claim without model_info must raise ValidationError."""
    with pytest.raises(ValidationError, match="requires model_info"):
        Claim(
            id="claim-prop-1",
            statement="Migrate to platformdirs",
            trust_level=TrustLevel.AI_PROPOSED,
            source=ClaimSource.MODEL,
            model_info=None,
        )


def test_ai_proposed_claim_with_tool_source_rejected() -> None:
    """AI_PROPOSED claim with TOOL source must raise ValidationError."""
    with pytest.raises(ValidationError, match="AI_PROPOSED claim source must be MODEL"):
        Claim(
            id="claim-prop-2",
            statement="Migrate to platformdirs",
            trust_level=TrustLevel.AI_PROPOSED,
            source=ClaimSource.TOOL,
            model_info=make_dummy_model_info(),
        )


def test_verified_refuted_without_supersedes_rejected() -> None:
    """VERIFIED and REFUTED claims must have supersedes set."""
    with pytest.raises(ValidationError, match="must supersede"):
        Claim(
            id="claim-ver-1",
            statement="Platformdirs is equivalent",
            trust_level=TrustLevel.AI_PROPOSED_VERIFIED,
            source=ClaimSource.TOOL,
            evidence_ids=["ev-1"],
            supersedes=None,
        )

    with pytest.raises(ValidationError, match="must supersede"):
        Claim(
            id="claim-ref-1",
            statement="Invalid replacement fails tests",
            trust_level=TrustLevel.AI_PROPOSED_REFUTED,
            source=ClaimSource.TOOL,
            evidence_ids=["ev-1"],
            supersedes=None,
        )


def test_verified_refuted_without_evidence_rejected() -> None:
    """VERIFIED and REFUTED claims must have evidence_ids."""
    with pytest.raises(ValidationError, match="requires evidence_ids"):
        Claim(
            id="claim-ver-2",
            statement="Platformdirs is equivalent",
            trust_level=TrustLevel.AI_PROPOSED_VERIFIED,
            source=ClaimSource.TOOL,
            evidence_ids=[],
            supersedes="claim-prop-1",
        )


def test_unverified_without_note_rejected() -> None:
    """UNVERIFIED claim without an explanatory note must raise ValidationError."""
    with pytest.raises(ValidationError, match="must have a note"):
        Claim(
            id="claim-unv-1",
            statement="Unchecked dependency note",
            trust_level=TrustLevel.UNVERIFIED,
            source=ClaimSource.HUMAN,
            note="",
        )


def test_model_info_mock_consistency() -> None:
    """ModelInfo with backend='mock' must have is_mock=True."""
    with pytest.raises(ValidationError, match="backend='mock' must have is_mock=True"):
        ModelInfo(
            model_id="gemma-mock",
            backend="mock",
            access_method="api",
            timestamp=datetime.now(UTC),
            is_mock=False,
        )


def test_evidence_path_validation() -> None:
    """Evidence path must be a clean relative path."""
    with pytest.raises(ValidationError, match="non-empty relative path"):
        Evidence(
            id="ev-bad-1",
            kind=EvidenceKind.OTHER,
            path="/etc/passwd",
            sha256="b" * 64,
            size_bytes=10,
            created_at=datetime.now(UTC),
            description="Bad path",
        )

    with pytest.raises(ValidationError, match="non-empty relative path"):
        Evidence(
            id="ev-bad-2",
            kind=EvidenceKind.OTHER,
            path="../outside.txt",
            sha256="b" * 64,
            size_bytes=10,
            created_at=datetime.now(UTC),
            description="Traversal path",
        )


def test_capsule_referential_integrity_dangling_evidence() -> None:
    """Capsule must reject dangling evidence_ids referenced in claims or signals."""
    claim = Claim(
        id="claim-1",
        statement="Observed claim",
        trust_level=TrustLevel.OBSERVED,
        source=ClaimSource.TOOL,
        evidence_ids=["ev-nonexistent"],
    )
    with pytest.raises(ValidationError, match="Dangling evidence_id 'ev-nonexistent'"):
        Capsule(
            dependency=DependencyInfo(name="jinja2"),
            claims=[claim],
            evidence=[],
        )


def test_capsule_referential_integrity_dangling_claim() -> None:
    """Capsule must reject dangling claim_ids referenced in risk signals or proposals."""
    sig = RiskSignal(
        name="deprecated_package",
        status=SignalStatus.AVAILABLE,
        points=25,
        detail="Package is unmaintained",
        claim_id="claim-missing",
    )
    with pytest.raises(ValidationError, match="Dangling claim_id 'claim-missing'"):
        Capsule(
            dependency=DependencyInfo(name="appdirs"),
            risk_indicators=[sig],
            risk_score=25,
            claims=[],
            evidence=[],
        )


def test_capsule_duplicate_ids_rejected() -> None:
    """Capsule must reject duplicate claim or evidence IDs."""
    ev1 = make_dummy_evidence("ev-dup")
    ev2 = make_dummy_evidence("ev-dup")

    with pytest.raises(ValidationError, match="Duplicate evidence ID in capsule: ev-dup"):
        Capsule(
            dependency=DependencyInfo(name="test"),
            evidence=[ev1, ev2],
        )


def test_capsule_risk_score_sum_points_validation() -> None:
    """Capsule risk_score must equal the exact sum of RiskSignal points."""
    sig1 = RiskSignal(name="sig1", points=15, detail="High risk")
    sig2 = RiskSignal(name="sig2", points=20, detail="Medium risk")

    with pytest.raises(ValidationError, match=r"risk_score \(50\) does not match sum"):
        Capsule(
            dependency=DependencyInfo(name="test"),
            risk_indicators=[sig1, sig2],
            risk_score=50,  # 15 + 20 != 50
        )

    capsule = Capsule(
        dependency=DependencyInfo(name="test"),
        risk_indicators=[sig1, sig2],
        risk_score=35,
    )
    assert capsule.risk_score == 35


def test_capsule_synthetic_forced_by_mock_model() -> None:
    """Capsule with mock ModelInfo must have synthetic=True."""
    mock_info = make_dummy_model_info(is_mock=True)

    with pytest.raises(ValidationError, match="must have synthetic=True when containing mock"):
        Capsule(
            dependency=DependencyInfo(name="test"),
            model_info=mock_info,
            synthetic=False,
        )

    capsule = Capsule(
        dependency=DependencyInfo(name="test"),
        model_info=mock_info,
        synthetic=True,
    )
    assert capsule.synthetic is True
