"""Data models for compatibility gaps, analysis limitations, and exit readiness."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from exitkit.ledger.models import Claim


class GapCategory(StrEnum):
    """Classification of observed compatibility gaps."""

    FAILING_TEST = "FAILING_TEST"
    BEHAVIORAL_DIFFERENCE = "BEHAVIORAL_DIFFERENCE"
    UNSUPPORTED_API = "UNSUPPORTED_API"
    PYTHON_VERSION_INCOMPATIBILITY = "PYTHON_VERSION_INCOMPATIBILITY"
    ENVIRONMENT_REGRESSION = "ENVIRONMENT_REGRESSION"


class GapSeverity(StrEnum):
    """Impact level of an observed gap on migration safety."""

    BLOCKER = "BLOCKER"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class LimitationCategory(StrEnum):
    """Classification of unavoidable analysis limitations."""

    NO_TESTS = "NO_TESTS"
    DYNAMIC_IMPORTS = "DYNAMIC_IMPORTS"
    NETWORK_DEPENDENT = "NETWORK_DEPENDENT"
    FLAKY_TESTS = "FLAKY_TESTS"


class ReadinessTier(StrEnum):
    """Actionable recommendation tier based on Exit Readiness Score."""

    READY_FOR_EXIT = "READY_FOR_EXIT"
    NEEDS_SHIM_OR_REVIEW = "NEEDS_SHIM_OR_REVIEW"
    HIGH_RISK_GAPS = "HIGH_RISK_GAPS"


class CompatibilityGap(BaseModel):
    """Structured compatibility gap discovered during real execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    category: GapCategory
    severity: GapSeverity
    description: str
    evidence_id: str
    affected_file: str | None = None
    affected_symbol: str | None = None


class AnalysisLimitation(BaseModel):
    """Transparent disclosure of what could not be fully verified."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    category: LimitationCategory
    description: str
    affected_scope: str | None = None


class GapTriage(BaseModel):
    """Gemma-proposed root cause analysis and mitigation for a gap."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    gap_id: str
    cause: str
    suggested_fix: str
    claim: Claim


class ReadinessBreakdown(BaseModel):
    """Transparent point breakdown of the Exit Readiness Score."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    test_suite_score: int = Field(ge=0, le=30)
    differential_score: int = Field(ge=0, le=30)
    python_support_score: int = Field(ge=0, le=20)
    api_surface_score: int = Field(ge=0, le=20)
    gap_penalties: int = Field(le=0)
    total_score: int = Field(ge=0, le=100)
    tier: ReadinessTier
    explanation: str


class ComparisonReport(BaseModel):
    """Complete gap comparison and evidence-only exit readiness report."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    target_dependency: str
    proposed_package: str
    readiness_score: int = Field(ge=0, le=100)
    breakdown: ReadinessBreakdown
    gaps: list[CompatibilityGap]
    limitations: list[AnalysisLimitation]
    triages: list[GapTriage]
    run_dir: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
