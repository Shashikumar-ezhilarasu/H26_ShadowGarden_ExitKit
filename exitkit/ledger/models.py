"""Capsule schema and sacred trust ledger models."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from exitkit import __version__


class TrustLevel(StrEnum):
    """Trust levels for statements in the ledger."""

    OBSERVED = "OBSERVED"
    AI_PROPOSED = "AI_PROPOSED"
    AI_PROPOSED_VERIFIED = "AI_PROPOSED_VERIFIED"
    AI_PROPOSED_REFUTED = "AI_PROPOSED_REFUTED"
    UNVERIFIED = "UNVERIFIED"


class ClaimSource(StrEnum):
    """Origin of a claim."""

    TOOL = "TOOL"
    MODEL = "MODEL"
    HUMAN = "HUMAN"


class EvidenceKind(StrEnum):
    """Categorization of evidence payloads."""

    HTTP_RESPONSE = "HTTP_RESPONSE"
    COMMAND_OUTPUT = "COMMAND_OUTPUT"
    TEST_RUN = "TEST_RUN"
    FILE_SNAPSHOT = "FILE_SNAPSHOT"
    TOOL_CALL_LOG = "TOOL_CALL_LOG"
    MODEL_RESPONSE = "MODEL_RESPONSE"
    DIFF = "DIFF"
    OTHER = "OTHER"


class SignalStatus(StrEnum):
    """Availability of an evidence signal."""

    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


class BaselineOutcome(StrEnum):
    """Outcome of running target tests out-of-the-box."""

    PASS_AS_DECLARED = "PASS_AS_DECLARED"
    FAIL_AS_DECLARED = "FAIL_AS_DECLARED"
    PASS_AFTER_MINIMAL_FIX = "PASS_AFTER_MINIMAL_FIX"
    NO_TESTS = "NO_TESTS"
    NOT_RUN = "NOT_RUN"


class ProposalKind(StrEnum):
    """Categorization of migration proposals."""

    UPGRADE = "UPGRADE"
    REPLACE = "REPLACE"
    REMOVE_OR_VENDOR = "REMOVE_OR_VENDOR"


class RunLabel(StrEnum):
    """Execution context for test runs."""

    BASELINE = "BASELINE"
    MIGRATED = "MIGRATED"
    DIFFERENTIAL = "DIFFERENTIAL"


class Evidence(BaseModel):
    """An immutable, content-addressed artifact stored in the evidence store."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    kind: EvidenceKind
    path: str
    sha256: str
    size_bytes: int
    created_at: datetime
    description: str
    command: str | None = None
    exit_code: int | None = None
    duration_seconds: float | None = None

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        clean = value.strip().replace("\\", "/")
        if not clean or clean.startswith("/") or ".." in clean.split("/"):
            raise ValueError(f"Evidence path must be a non-empty relative path: {value}")
        return clean

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        clean = value.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", clean):
            msg = f"Evidence sha256 must be a 64-char lowercase hex string: {value}"
            raise ValueError(msg)
        return clean

    @field_validator("size_bytes")
    @classmethod
    def validate_size(cls, value: int) -> int:
        if value < 0:
            raise ValueError(f"Evidence size_bytes cannot be negative: {value}")
        return value

    @field_validator("created_at")
    @classmethod
    def validate_tz(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class ModelInfo(BaseModel):
    """Metadata describing the AI model and invocation context."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model_id: str
    backend: Literal["google", "ollama", "mock"]
    access_method: str
    timestamp: datetime
    is_mock: bool = False

    @model_validator(mode="after")
    def validate_mock_consistency(self) -> ModelInfo:
        if self.backend == "mock" and not self.is_mock:
            raise ValueError("ModelInfo with backend='mock' must have is_mock=True")
        return self


class Claim(BaseModel):
    """A discrete proposition tracked in the sacred trust ledger."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    statement: str
    trust_level: TrustLevel
    source: ClaimSource
    evidence_ids: list[str] = Field(default_factory=list)
    model_info: ModelInfo | None = None
    supersedes: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def validate_claim_integrity(self) -> Claim:
        if self.trust_level == TrustLevel.OBSERVED:
            if self.source != ClaimSource.TOOL:
                raise ValueError("OBSERVED claim source must be TOOL")
            if not self.evidence_ids:
                raise ValueError("OBSERVED claim requires at least one evidence_id")

        elif self.trust_level == TrustLevel.AI_PROPOSED:
            if self.source != ClaimSource.MODEL:
                raise ValueError("AI_PROPOSED claim source must be MODEL")
            if self.model_info is None:
                raise ValueError("AI_PROPOSED claim requires model_info")

        elif self.trust_level in (TrustLevel.AI_PROPOSED_VERIFIED, TrustLevel.AI_PROPOSED_REFUTED):
            if not self.evidence_ids:
                raise ValueError(f"{self.trust_level.value} claim requires evidence_ids")
            if not self.supersedes:
                raise ValueError(
                    f"{self.trust_level.value} claim must supersede an earlier AI_PROPOSED claim"
                )

        elif self.trust_level == TrustLevel.UNVERIFIED:
            if not self.note or not self.note.strip():
                raise ValueError(
                    "UNVERIFIED claim must have a note explaining its unverified status"
                )

        return self


class EnvironmentInfo(BaseModel):
    """Execution environment details."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    python_version: str
    platform: str
    uv_version: str | None = None


class TargetInfo(BaseModel):
    """Target repository reference and integrity fingerprint."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    repo_ref: str
    git_commit: str | None = None
    tree_fingerprint: str | None = None


class DeclaredSpecifier(BaseModel):
    """A dependency version constraint declared in a project manifest."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    file: str
    specifier: str


class DependencyInfo(BaseModel):
    """Dependency under analysis."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    ecosystem: str = "pypi"
    declared_specifiers: list[DeclaredSpecifier] = Field(default_factory=list)
    resolved_version: str | None = None
    manifest_divergence: bool = False
    divergence_details: str | None = None


class RiskSignal(BaseModel):
    """A scored risk indicator backed by evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    status: SignalStatus = SignalStatus.AVAILABLE
    points: int = 0
    detail: str
    claim_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class UsageLocation(BaseModel):
    """A site where the target code consumes the dependency."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    file: str
    line: int
    symbol: str
    snippet: str
    is_test: bool = False


class BlastRadius(BaseModel):
    """Aggregate statistics on dependency blast radius."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    files_touched: int = 0
    total_source_files: int = 0
    call_sites: int = 0
    test_sites: int = 0
    source_sites: int = 0


class BaselineRun(BaseModel):
    """Observed baseline test suite results on the unmigrated repository."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: BaselineOutcome = BaselineOutcome.NOT_RUN
    evidence_ids: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    minimal_fix: str | None = None


class ApiMappingItem(BaseModel):
    """Translation between an old symbol and its proposed replacement."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    old_symbol: str
    new_symbol: str
    claim_id: str


class MigrationStep(BaseModel):
    """An ordered action item in the proposed migration plan."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    order: int
    description: str
    claim_id: str


class Proposal(BaseModel):
    """Gemma-proposed migration plan and compatibility shim."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: ProposalKind
    replacement_name: str | None = None
    target_version: str | None = None
    rationale_claim_ids: list[str] = Field(default_factory=list)
    api_mapping: list[ApiMappingItem] = Field(default_factory=list)
    migration_steps: list[MigrationStep] = Field(default_factory=list)
    shim_files: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class SuggestedTest(BaseModel):
    """A proposed test case to verify migration equivalence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    path: str
    claim_id: str


class ExecutedResult(BaseModel):
    """Outcome of executing a baseline, migrated, or differential test suite."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: RunLabel
    python_version: str
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    duration_seconds: float = 0.0
    exit_code: int = 0
    evidence_id: str | None = None
    claim_id: str | None = None


class CompatibilityGap(BaseModel):
    """A behavioral divergence or missing capability between old and new dependencies."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    description: str
    severity: str
    claim_id: str


class Capsule(BaseModel):
    """Top-level Dependency Exit Capsule container."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = "1.0"
    capsule_id: str = Field(default_factory=lambda: f"cap-{uuid4().hex[:12]}")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    exitkit_version: str = Field(default=__version__)
    synthetic: bool = False

    environment: EnvironmentInfo = Field(
        default_factory=lambda: EnvironmentInfo(
            python_version="3.12",
            platform="unknown",
        )
    )
    target: TargetInfo = Field(default_factory=lambda: TargetInfo(repo_ref="unknown"))
    dependency: DependencyInfo = Field(default_factory=lambda: DependencyInfo(name="unknown"))
    risk_indicators: list[RiskSignal] = Field(default_factory=list)
    risk_score: int = 0
    usage_locations: list[UsageLocation] = Field(default_factory=list)
    blast_radius: BlastRadius | None = None
    baseline: BaselineRun = Field(default_factory=BaselineRun)
    proposal: Proposal | None = None
    suggested_tests: list[SuggestedTest] = Field(default_factory=list)
    executed_results: list[ExecutedResult] = Field(default_factory=list)
    compatibility_gaps: list[CompatibilityGap] = Field(default_factory=list)
    open_concerns: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    model_info: ModelInfo | None = None
    claims: list[Claim] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_capsule(self, info: ValidationInfo) -> Capsule:
        # 1. Evidence ID uniqueness
        ev_ids: set[str] = set()
        for ev in self.evidence:
            if ev.id in ev_ids:
                raise ValueError(f"Duplicate evidence ID in capsule: {ev.id}")
            ev_ids.add(ev.id)

        # 2. Claim ID uniqueness
        claim_ids: set[str] = set()
        for cl in self.claims:
            if cl.id in claim_ids:
                raise ValueError(f"Duplicate claim ID in capsule: {cl.id}")
            claim_ids.add(cl.id)

        # 3. Referential integrity: evidence IDs
        def check_evidence_id(eid: str, location: str) -> None:
            if eid not in ev_ids:
                raise ValueError(f"Dangling evidence_id '{eid}' referenced in {location}")

        for cl in self.claims:
            for eid in cl.evidence_ids:
                check_evidence_id(eid, f"claim {cl.id}")

        for sig in self.risk_indicators:
            for eid in sig.evidence_ids:
                check_evidence_id(eid, f"risk_indicator {sig.name}")

        for eid in self.baseline.evidence_ids:
            check_evidence_id(eid, "baseline.evidence_ids")

        for res in self.executed_results:
            if res.evidence_id:
                check_evidence_id(res.evidence_id, f"executed_result {res.label}")

        # 4. Referential integrity: claim IDs
        def check_claim_id(cid: str, location: str) -> None:
            if cid not in claim_ids:
                raise ValueError(f"Dangling claim_id '{cid}' referenced in {location}")

        for cl in self.claims:
            if cl.supersedes:
                check_claim_id(cl.supersedes, f"claim {cl.id}.supersedes")

        for sig in self.risk_indicators:
            if sig.claim_id:
                check_claim_id(sig.claim_id, f"risk_indicator {sig.name}")

        for cid in self.baseline.claim_ids:
            check_claim_id(cid, "baseline.claim_ids")

        if self.proposal:
            for cid in self.proposal.rationale_claim_ids:
                check_claim_id(cid, "proposal.rationale_claim_ids")
            for item in self.proposal.api_mapping:
                check_claim_id(item.claim_id, f"proposal.api_mapping {item.old_symbol}")
            for step in self.proposal.migration_steps:
                check_claim_id(step.claim_id, f"proposal.migration_step {step.order}")
            for cid in self.proposal.assumptions:
                check_claim_id(cid, "proposal.assumptions")

        for test in self.suggested_tests:
            check_claim_id(test.claim_id, f"suggested_test {test.name}")

        for res in self.executed_results:
            if res.claim_id:
                check_claim_id(res.claim_id, f"executed_result {res.label}")

        for gap in self.compatibility_gaps:
            check_claim_id(gap.claim_id, f"compatibility_gap {gap.description}")

        # Check open_concerns and limitations if they contain claim IDs
        for concern in self.open_concerns:
            words = concern.replace(":", " ").replace(",", " ").split()
            for word in words:
                if word.startswith("claim-") and word not in claim_ids:
                    raise ValueError(f"Dangling claim_id '{word}' in open_concerns: {concern}")

        for limitation in self.limitations:
            words = limitation.replace(":", " ").replace(",", " ").split()
            for word in words:
                if word.startswith("claim-") and word not in claim_ids:
                    raise ValueError(f"Dangling claim_id '{word}' in limitations: {limitation}")

        # 5. Synthetic check: true if any ModelInfo.is_mock
        has_mock = False
        if self.model_info and self.model_info.is_mock:
            has_mock = True
        for cl in self.claims:
            if cl.model_info and cl.model_info.is_mock:
                has_mock = True
                break

        if has_mock and not self.synthetic:
            raise ValueError("Capsule must have synthetic=True when containing mock ModelInfo")

        # 6. Risk score equals sum of RiskSignal points
        sum_points = sum(sig.points for sig in self.risk_indicators)
        if self.risk_score != sum_points:
            msg = (
                f"risk_score ({self.risk_score}) does not match "
                f"sum of risk signal points ({sum_points})"
            )
            raise ValueError(msg)

        # 7. Context-driven hash verification (evidence_root)
        if info.context and "evidence_root" in info.context:
            root = Path(info.context["evidence_root"])
            ev_map = {ev.id: ev for ev in self.evidence}

            for cl in self.claims:
                if cl.trust_level in (
                    TrustLevel.OBSERVED,
                    TrustLevel.AI_PROPOSED_VERIFIED,
                    TrustLevel.AI_PROPOSED_REFUTED,
                ):
                    for eid in cl.evidence_ids:
                        ev = ev_map[eid]
                        ev_file = root / ev.path
                        if not ev_file.exists():
                            err_msg = (
                                f"Evidence file missing on disk: {ev_file} "
                                f"(for {cl.trust_level.value} claim {cl.id})"
                            )
                            raise ValueError(err_msg)
                        content = ev_file.read_bytes()
                        hasher = hashlib.sha256(content)
                        if hasher.hexdigest() != ev.sha256:
                            err_msg = (
                                f"Evidence hash mismatch for {ev.id}: "
                                f"expected {ev.sha256}, got {hasher.hexdigest()}"
                            )
                            raise ValueError(err_msg)

        return self

    def to_canonical_json(self) -> str:
        """Produce a deterministic, canonical JSON representation with sorted keys."""

        def _json_serial(obj: Any) -> Any:
            if isinstance(obj, datetime):
                utc_dt = obj.astimezone(UTC)
                return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            if isinstance(obj, StrEnum):
                return obj.value
            raise TypeError(f"Type {type(obj)} not serializable")

        data = self.model_dump(mode="json")
        return json.dumps(
            data,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            default=_json_serial,
        )

    def capsule_digest(self) -> str:
        """Calculate the SHA-256 digest of the canonical JSON capsule."""
        return hashlib.sha256(self.to_canonical_json().encode("utf-8")).hexdigest()
