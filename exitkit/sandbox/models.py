"""Data models for sandboxed execution, test runs, and differential testing."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from exitkit.ledger.models import BaselineOutcome, Claim, Evidence, RunLabel


class CommandRunResult(BaseModel):
    """Result of running a single command inside a sandbox environment."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timeout_hit: bool = False


class TestRunResult(BaseModel):
    """Structured outcome of running test suite in baseline or migrated state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: RunLabel
    passed: bool
    baseline_outcome: BaselineOutcome | None = None
    tests_ran: int = 0
    passed_count: int = 0
    failed_count: int = 0
    error_count: int = 0
    skipped_count: int = 0
    duration_seconds: float
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    evidence_id: str
    failure_details: list[str] = Field(default_factory=list)


class DifferentialCaseResult(BaseModel):
    """Comparison of original vs replacement dependency on a single input case."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    input_repr: str
    original_output: str | None = None
    replacement_output: str | None = None
    original_error: str | None = None
    replacement_error: str | None = None
    matched: bool
    difference_detail: str | None = None


class DifferentialSuiteResult(BaseModel):
    """Aggregate result of running differential test suite."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    target_dependency: str
    replacement_dependency: str
    all_matched: bool
    total_cases: int
    matched_cases: int
    duration_seconds: float
    cases: list[DifferentialCaseResult]
    evidence_id: str


class SandboxVerificationResult(BaseModel):
    """Complete record of a sandboxed verification run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    target_dependency: str
    target_repo: str
    python_version: str
    baseline: TestRunResult
    migrated: TestRunResult
    differential: DifferentialSuiteResult
    unified_patch: str
    patch_evidence_id: str
    proposal_claim: Claim
    resulting_claim: Claim
    verified: bool
    evidence_items: list[Evidence]
    run_dir: str
