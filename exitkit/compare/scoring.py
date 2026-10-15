"""Evidence-only exit readiness scoring algorithm."""

from __future__ import annotations

from exitkit.compare.models import (
    CompatibilityGap,
    GapSeverity,
    ReadinessBreakdown,
    ReadinessTier,
)
from exitkit.ledger.models import BaselineOutcome
from exitkit.sandbox.models import DifferentialSuiteResult, TestRunResult


def compute_exit_readiness_score(
    baseline: TestRunResult,
    migrated: TestRunResult,
    differential: DifferentialSuiteResult,
    py312_supported: bool = True,
    py313_supported: bool = True,
    gaps: list[CompatibilityGap] | None = None,
    claim_refuted: bool = False,
) -> ReadinessBreakdown:
    """Compute an objective 0-100 score strictly from observed execution evidence.

    Rule: AI_PROPOSED claims are NEVER used as positive evidence.
    """
    effective_gaps = gaps or []

    # 1. Test Suite Health & Regression (max 30 pts)
    test_suite_score = 0
    if baseline.baseline_outcome == BaselineOutcome.NO_TESTS or migrated.tests_ran == 0:
        test_suite_score = 0
    elif migrated.passed and (migrated.failed_count + migrated.error_count == 0):
        test_suite_score = 30
    else:
        # Partial test pass
        total_tests = migrated.tests_ran
        if total_tests > 0:
            pass_ratio = migrated.passed_count / total_tests
            test_suite_score = round(pass_ratio * 15)

    # 2. Differential Parity Pass Rate (max 30 pts)
    diff_score = 0
    if differential.total_cases > 0:
        parity_ratio = differential.matched_cases / differential.total_cases
        diff_score = round(parity_ratio * 30)

    # 3. Python 3.12 / 3.13 Support (max 20 pts)
    py_score = 0
    if py312_supported:
        py_score += 10
    if py313_supported:
        py_score += 10

    # 4. API Surface Coverage (max 20 pts)
    # Deduct if unsupported API gaps exist
    unsupported_count = sum(1 for g in effective_gaps if g.category.value == "UNSUPPORTED_API")
    api_score = max(0, 20 - (unsupported_count * 10))

    # 5. Gap Penalties & Refuted Claim Deductions
    penalties = 0
    for g in effective_gaps:
        if g.severity == GapSeverity.BLOCKER:
            penalties -= 30
        elif g.severity == GapSeverity.HIGH:
            penalties -= 15
        elif g.severity == GapSeverity.MEDIUM:
            penalties -= 5
        elif g.severity == GapSeverity.LOW:
            penalties -= 2

    if claim_refuted:
        penalties -= 20

    raw_total = test_suite_score + diff_score + py_score + api_score + penalties
    total_score = max(0, min(100, raw_total))

    # Determine Tier
    if total_score >= 85:
        tier = ReadinessTier.READY_FOR_EXIT
    elif total_score >= 60:
        tier = ReadinessTier.NEEDS_SHIM_OR_REVIEW
    else:
        tier = ReadinessTier.HIGH_RISK_GAPS

    explanation = (
        f"Tests: {test_suite_score}/30 | Parity: {diff_score}/30 | "
        f"Py3.12/3.13: {py_score}/20 | API: {api_score}/20 | "
        f"Penalties: {penalties} -> Total: {total_score}/100"
    )

    return ReadinessBreakdown(
        test_suite_score=test_suite_score,
        differential_score=diff_score,
        python_support_score=py_score,
        api_surface_score=api_score,
        gap_penalties=penalties,
        total_score=total_score,
        tier=tier,
        explanation=explanation,
    )
