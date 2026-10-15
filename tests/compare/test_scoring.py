"""Tests for deterministic evidence-only exit readiness scoring."""

from __future__ import annotations

from exitkit.compare.models import (
    CompatibilityGap,
    GapCategory,
    GapSeverity,
    ReadinessTier,
)
from exitkit.compare.scoring import compute_exit_readiness_score
from exitkit.ledger.models import BaselineOutcome, RunLabel
from exitkit.sandbox.models import DifferentialCaseResult, DifferentialSuiteResult, TestRunResult

TestRunResult.__test__ = False


def _make_test_result(
    passed: bool,
    tests_ran: int,
    passed_count: int,
    failed_count: int = 0,
    outcome: BaselineOutcome = BaselineOutcome.PASS_AS_DECLARED,
) -> TestRunResult:
    return TestRunResult(
        label=RunLabel.BASELINE,
        passed=passed,
        exit_code=0 if passed else 1,
        tests_ran=tests_ran,
        passed_count=passed_count,
        failed_count=failed_count,
        error_count=0,
        skipped_count=0,
        duration_seconds=1.0,
        command=["pytest"],
        stdout="",
        stderr="",
        baseline_outcome=outcome,
        evidence_id="ev-test-1234",
    )


def _make_diff_result(matched: int, total: int) -> DifferentialSuiteResult:
    cases = []
    for i in range(total):
        is_match = i < matched
        cases.append(
            DifferentialCaseResult(
                name=f"case_{i}",
                input_repr="()",
                matched=is_match,
                original_output="val" if is_match else "val1",
                replacement_output="val" if is_match else "val2",
                difference_detail=None if is_match else "Divergent output",
            )
        )
    return DifferentialSuiteResult(
        target_dependency="dep_a",
        replacement_dependency="dep_b",
        total_cases=total,
        matched_cases=matched,
        duration_seconds=1.0,
        all_matched=(matched == total),
        cases=cases,
        evidence_id="ev-diff-1234",
    )


def test_deterministic_scoring_identical_output() -> None:
    """Scoring function must produce the exact same result on identical inputs."""
    baseline = _make_test_result(passed=True, tests_ran=10, passed_count=10)
    migrated = _make_test_result(passed=True, tests_ran=10, passed_count=10)
    diff = _make_diff_result(matched=5, total=5)

    res1 = compute_exit_readiness_score(baseline=baseline, migrated=migrated, differential=diff)
    res2 = compute_exit_readiness_score(baseline=baseline, migrated=migrated, differential=diff)

    assert res1.total_score == res2.total_score == 100
    assert res1.tier == res2.tier == ReadinessTier.READY_FOR_EXIT
    assert res1.explanation == res2.explanation


def test_missing_tests_produce_zero_test_score_and_lowered_readiness() -> None:
    """When repo has NO_TESTS or tests_ran == 0, test suite score is 0, not a free pass."""
    baseline = _make_test_result(
        passed=True, tests_ran=0, passed_count=0, outcome=BaselineOutcome.NO_TESTS
    )
    migrated = _make_test_result(passed=True, tests_ran=0, passed_count=0)
    diff = _make_diff_result(matched=5, total=5)

    breakdown = compute_exit_readiness_score(
        baseline=baseline, migrated=migrated, differential=diff
    )

    assert breakdown.test_suite_score == 0
    assert breakdown.differential_score == 30
    assert breakdown.python_support_score == 20
    assert breakdown.api_surface_score == 20
    assert breakdown.total_score == 70
    assert breakdown.tier == ReadinessTier.NEEDS_SHIM_OR_REVIEW


def test_refuted_claim_lowers_score() -> None:
    """When an AI proposal is refuted by observed evidence, it lowers the readiness score."""
    baseline = _make_test_result(passed=True, tests_ran=10, passed_count=10)
    migrated = _make_test_result(passed=True, tests_ran=10, passed_count=10)
    diff = _make_diff_result(matched=5, total=5)

    normal_score = compute_exit_readiness_score(
        baseline=baseline, migrated=migrated, differential=diff, claim_refuted=False
    )
    refuted_score = compute_exit_readiness_score(
        baseline=baseline, migrated=migrated, differential=diff, claim_refuted=True
    )

    assert refuted_score.total_score < normal_score.total_score
    assert refuted_score.gap_penalties == -20
    assert refuted_score.total_score == 80
    assert refuted_score.tier == ReadinessTier.NEEDS_SHIM_OR_REVIEW


def test_compatibility_gap_penalties() -> None:
    """Gap severities deduct correct penalty points."""
    baseline = _make_test_result(passed=True, tests_ran=10, passed_count=10)
    migrated = _make_test_result(passed=False, tests_ran=10, passed_count=8, failed_count=2)
    diff = _make_diff_result(matched=3, total=5)

    blocker_gap = CompatibilityGap(
        id="gap-1",
        category=GapCategory.FAILING_TEST,
        severity=GapSeverity.BLOCKER,
        description="Critical failure",
        evidence_id="ev-1",
    )
    high_gap = CompatibilityGap(
        id="gap-2",
        category=GapCategory.BEHAVIORAL_DIFFERENCE,
        severity=GapSeverity.HIGH,
        description="Differential mismatch",
        evidence_id="ev-2",
    )

    breakdown = compute_exit_readiness_score(
        baseline=baseline,
        migrated=migrated,
        differential=diff,
        gaps=[blocker_gap, high_gap],
    )

    # Blocker = -30, High = -15 -> penalties = -45
    assert breakdown.gap_penalties == -45
    assert breakdown.total_score < 60
    assert breakdown.tier == ReadinessTier.HIGH_RISK_GAPS
