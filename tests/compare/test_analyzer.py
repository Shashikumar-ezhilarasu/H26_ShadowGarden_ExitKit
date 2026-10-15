"""Tests for compatibility gap extraction, limitations, and comparison report generation."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from exitkit.compare.analyzer import (
    extract_compatibility_gaps,
    extract_limitations,
    run_comparison,
)
from exitkit.compare.models import GapCategory, GapSeverity, LimitationCategory
from exitkit.ledger.models import (
    BaselineOutcome,
    Claim,
    ClaimSource,
    ModelInfo,
    RunLabel,
    TrustLevel,
)
from exitkit.sandbox.models import (
    DifferentialCaseResult,
    DifferentialSuiteResult,
    SandboxVerificationResult,
    TestRunResult,
)

TestRunResult.__test__ = False


def _build_mock_verification(
    tmp_path: Path,
    migrated_passed: bool = True,
    failed_tests: list[str] | None = None,
    diff_matched: bool = True,
    no_tests: bool = False,
) -> SandboxVerificationResult:
    failures = failed_tests or []
    baseline = TestRunResult(
        label=RunLabel.BASELINE,
        passed=True,
        exit_code=0,
        tests_ran=0 if no_tests else 10,
        passed_count=0 if no_tests else 10,
        failed_count=0,
        error_count=0,
        skipped_count=0,
        duration_seconds=1.0,
        command=["pytest"],
        stdout="",
        stderr="",
        baseline_outcome=BaselineOutcome.NO_TESTS if no_tests else BaselineOutcome.PASS_AS_DECLARED,
        evidence_id="ev-base-1",
    )
    migrated = TestRunResult(
        label=RunLabel.MIGRATED,
        passed=migrated_passed,
        exit_code=0 if migrated_passed else 1,
        tests_ran=0 if no_tests else 10,
        passed_count=0 if no_tests else (10 - len(failures)),
        failed_count=len(failures),
        error_count=0,
        skipped_count=0,
        failure_details=failures,
        duration_seconds=1.0,
        command=["pytest"],
        stdout="",
        stderr="",
        evidence_id="ev-mig-1",
    )
    cases = [
        DifferentialCaseResult(
            name="user_data_dir",
            input_repr="('testapp', 'author')",
            matched=diff_matched,
            original_output="/home/user/.local/share/testapp",
            replacement_output=(
                "/home/user/.local/share/testapp" if diff_matched else "/var/testapp"
            ),
            difference_detail=None if diff_matched else "Divergent path resolution",
        )
    ]
    diff = DifferentialSuiteResult(
        target_dependency="appdirs",
        replacement_dependency="platformdirs",
        total_cases=1,
        matched_cases=1 if diff_matched else 0,
        duration_seconds=1.0,
        all_matched=diff_matched,
        cases=cases,
        evidence_id="ev-diff-1",
    )
    mock_model = ModelInfo(
        model_id="mock-gemma",
        backend="mock",
        access_method="mock",
        timestamp=datetime.now(UTC),
        is_mock=True,
    )
    prop_claim = Claim(
        id="claim-prop-1",
        statement="Migrate appdirs to platformdirs",
        trust_level=TrustLevel.AI_PROPOSED,
        source=ClaimSource.MODEL,
        model_info=mock_model,
    )
    res_claim = Claim(
        id="claim-res-1",
        statement="Resulting claim",
        trust_level=(
            TrustLevel.AI_PROPOSED_VERIFIED
            if (migrated_passed and diff_matched)
            else TrustLevel.AI_PROPOSED_REFUTED
        ),
        source=ClaimSource.TOOL,
        evidence_ids=["ev-mig-1", "ev-diff-1"],
        supersedes="claim-prop-1",
    )

    return SandboxVerificationResult(
        target_dependency="appdirs",
        target_repo=str(tmp_path),
        python_version="3.12",
        baseline=baseline,
        migrated=migrated,
        differential=diff,
        unified_patch="--- a/file.py\n+++ b/file.py",
        patch_evidence_id="ev-patch-1",
        proposal_claim=prop_claim,
        resulting_claim=res_claim,
        verified=(migrated_passed and diff_matched),
        evidence_items=[],
        run_dir=str(tmp_path),
    )


def test_extract_compatibility_gaps_finds_failures(tmp_path: Path) -> None:
    """extract_compatibility_gaps must extract test failures and diff mismatches."""
    verification = _build_mock_verification(
        tmp_path,
        migrated_passed=False,
        failed_tests=["tests/test_report.py::test_render FAILED soft_unicode missing"],
        diff_matched=False,
    )

    gaps = extract_compatibility_gaps(verification)
    assert len(gaps) == 2

    # Test failure gap
    test_gap = next(g for g in gaps if g.category == GapCategory.FAILING_TEST)
    assert test_gap.severity == GapSeverity.BLOCKER
    assert "soft_unicode" in test_gap.description
    assert test_gap.affected_file == "tests/test_report.py"
    assert test_gap.affected_symbol == "test_render"
    assert test_gap.evidence_id == "ev-mig-1"

    # Diff mismatch gap
    diff_gap = next(g for g in gaps if g.category == GapCategory.BEHAVIORAL_DIFFERENCE)
    assert diff_gap.severity == GapSeverity.HIGH
    assert diff_gap.affected_symbol == "user_data_dir"
    assert diff_gap.evidence_id == "ev-diff-1"


def test_extract_limitations_discloses_no_tests(tmp_path: Path) -> None:
    """extract_limitations plainly discloses when no tests ran."""
    verification = _build_mock_verification(tmp_path, no_tests=True)
    limitations = extract_limitations(tmp_path, "appdirs", verification)

    assert any(lim.category == LimitationCategory.NO_TESTS for lim in limitations)


def test_run_comparison_generates_report_and_file(tmp_path: Path) -> None:
    """run_comparison creates ComparisonReport and writes comparison.json."""
    verification = _build_mock_verification(
        tmp_path,
        migrated_passed=True,
        diff_matched=True,
    )

    report = run_comparison(
        repo_path=tmp_path,
        dep_name="appdirs",
        verification=verification,
        backend="mock",
        run_dir=tmp_path,
    )

    assert report.target_dependency == "appdirs"
    assert report.readiness_score == 100
    assert report.breakdown.tier.value == "READY_FOR_EXIT"

    # Check comparison.json was persisted
    comp_file = tmp_path / "comparison.json"
    assert comp_file.exists()
    data = json.loads(comp_file.read_text(encoding="utf-8"))
    assert data["readiness_score"] == 100
    assert data["target_dependency"] == "appdirs"
