"""Comparison engine analyzing gaps, limitations, triage, and exit readiness."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Literal

from exitkit.compare.models import (
    AnalysisLimitation,
    ComparisonReport,
    CompatibilityGap,
    GapCategory,
    GapSeverity,
    GapTriage,
    LimitationCategory,
)
from exitkit.compare.scoring import compute_exit_readiness_score
from exitkit.ledger.models import BaselineOutcome, Claim, ClaimSource, TrustLevel
from exitkit.ledger.store import EvidenceStore
from exitkit.llm.client import get_model_client
from exitkit.sandbox.executor import run_sandbox_verification
from exitkit.sandbox.models import SandboxVerificationResult


def extract_compatibility_gaps(
    verification: SandboxVerificationResult,
) -> list[CompatibilityGap]:
    """Identify observed compatibility gaps from test failures and differential mismatches."""
    gaps: list[CompatibilityGap] = []
    migrated = verification.migrated
    diff = verification.differential

    # 1. Migrated test suite failures
    if not migrated.passed or migrated.failed_count > 0:
        if migrated.failure_details:
            for idx, detail in enumerate(migrated.failure_details, start=1):
                file_part = None
                sym_part = None
                if "::" in detail:
                    parts = detail.split("::", 1)
                    file_part = parts[0].strip()
                    sym_part = parts[1].split()[0].strip()

                is_blocker = (
                    verification.baseline.passed or migrated.tests_ran == migrated.failed_count
                )
                severity = GapSeverity.BLOCKER if is_blocker else GapSeverity.HIGH

                gaps.append(
                    CompatibilityGap(
                        id=f"gap-test-{idx}",
                        category=GapCategory.FAILING_TEST,
                        severity=severity,
                        description=f"Migrated test failure: {detail}",
                        evidence_id=migrated.evidence_id,
                        affected_file=file_part,
                        affected_symbol=sym_part,
                    )
                )
        else:
            gaps.append(
                CompatibilityGap(
                    id="gap-test-migrated-failed",
                    category=GapCategory.FAILING_TEST,
                    severity=GapSeverity.BLOCKER,
                    description=f"Migrated test suite failed with exit code {migrated.exit_code}",
                    evidence_id=migrated.evidence_id,
                )
            )

    # 2. Differential parity mismatches
    for idx, case in enumerate(diff.cases, start=1):
        if not case.matched:
            err_msg = case.difference_detail or case.replacement_error or ""
            err_lower = err_msg.lower()
            unsupported_markers = ("notimplementederror", "attributeerror", "unsupported")
            if any(marker in err_lower for marker in unsupported_markers):
                cat = GapCategory.UNSUPPORTED_API
                sev = GapSeverity.BLOCKER
            else:
                cat = GapCategory.BEHAVIORAL_DIFFERENCE
                sev = GapSeverity.HIGH

            desc = (
                f"Parity mismatch on {case.name}({case.input_repr}): "
                f"{err_msg or 'Output differed from baseline'}"
            )
            gaps.append(
                CompatibilityGap(
                    id=f"gap-diff-{idx}",
                    category=cat,
                    severity=sev,
                    description=desc,
                    evidence_id=diff.evidence_id,
                    affected_symbol=case.name,
                )
            )

    return gaps


def _find_dynamic_import_sites(repo_path: Path) -> list[str]:
    """Scan Python files for dynamic import mechanisms like __import__ or importlib."""
    dynamic_sites: list[str] = []
    if not repo_path.is_dir():
        return dynamic_sites

    for py_file in repo_path.rglob("*.py"):
        if any(part.startswith(".") for part in py_file.parts):
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8", errors="ignore"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func_name = ""
                    if isinstance(node.func, ast.Name):
                        func_name = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        func_name = node.func.attr
                    if func_name in ("__import__", "import_module"):
                        dynamic_sites.append(f"{py_file.name}:{node.lineno}")
        except Exception:
            continue
    return dynamic_sites


def extract_limitations(
    repo_path: Path | str,
    dep_name: str,
    verification: SandboxVerificationResult,
) -> list[AnalysisLimitation]:
    """Honest disclosure of unexercised tests and static analysis limitations."""
    limitations: list[AnalysisLimitation] = []
    target_path = Path(repo_path)

    # 1. Missing or unexecuted tests
    if (
        verification.baseline.baseline_outcome == BaselineOutcome.NO_TESTS
        or verification.migrated.tests_ran == 0
    ):
        limitations.append(
            AnalysisLimitation(
                id="lim-no-tests",
                category=LimitationCategory.NO_TESTS,
                description=(
                    "Target repository has no automated tests discovered or tests could not run."
                ),
                affected_scope="test_suite",
            )
        )

    # 2. Dynamic AST imports
    dynamic_sites = _find_dynamic_import_sites(target_path)
    if dynamic_sites:
        limitations.append(
            AnalysisLimitation(
                id="lim-dynamic-imports",
                category=LimitationCategory.DYNAMIC_IMPORTS,
                description=(
                    f"Target uses {len(dynamic_sites)} dynamic import sites "
                    "(__import__, import_module) which cannot be statically verified."
                ),
                affected_scope=dep_name,
            )
        )

    # 3. Flaky or external network signs
    stdout_all = (verification.baseline.stdout + " " + verification.migrated.stdout).lower()
    if "connection refused" in stdout_all or "timed out" in stdout_all:
        limitations.append(
            AnalysisLimitation(
                id="lim-network",
                category=LimitationCategory.NETWORK_DEPENDENT,
                description=(
                    "Test suite encountered external network or socket connectivity operations."
                ),
                affected_scope="network",
            )
        )

    return limitations


def run_comparison(
    repo_path: Path | str,
    dep_name: str,
    verification: SandboxVerificationResult | None = None,
    backend: Literal["google", "ollama", "mock"] | None = None,
    model_id: str | None = None,
    run_dir: Path | str | None = None,
    python_version: str = "3.12",
) -> ComparisonReport:
    """Run compatibility gap analysis, Gemma triage, and evidence-only exit readiness."""
    target_path = Path(repo_path).resolve()
    effective_run_dir = Path(run_dir) if run_dir else EvidenceStore.create_run_dir()

    # 1. Obtain verification result (run or load)
    active_verification = verification
    if not active_verification:
        v_file = effective_run_dir / "verification.json"
        if v_file.exists():
            active_verification = SandboxVerificationResult.model_validate_json(
                v_file.read_text(encoding="utf-8")
            )
        else:
            active_verification = run_sandbox_verification(
                repo_path=target_path,
                dep_name=dep_name,
                python_version=python_version,
                run_dir=effective_run_dir,
                backend=backend,
            )

    # 2. Extract observed gaps and honest limitations
    gaps = extract_compatibility_gaps(active_verification)
    limitations = extract_limitations(target_path, dep_name, active_verification)

    # 3. Gap triage via Gemma 4 (or configured backend)
    triages: list[GapTriage] = []
    if gaps:
        client = get_model_client(backend=backend, model_id=model_id)
        for g in gaps:
            # Provide context from stdout if available
            ctx = (
                active_verification.migrated.stdout[:1000]
                if g.category == GapCategory.FAILING_TEST
                else g.description
            )
            cause, fix, model_info = client.generate_gap_triage(
                gap_id=g.id,
                category=g.category.value,
                description=g.description,
                failing_context=ctx,
            )
            triage_claim = Claim(
                id=f"claim-triage-{g.id}",
                statement=f"Triage for {g.id}: {cause} Suggested fix: {fix}",
                trust_level=TrustLevel.AI_PROPOSED,
                source=ClaimSource.MODEL,
                evidence_ids=[g.evidence_id],
                model_info=model_info,
            )
            triages.append(
                GapTriage(
                    gap_id=g.id,
                    cause=cause,
                    suggested_fix=fix,
                    claim=triage_claim,
                )
            )

    # 4. Compute deterministic evidence-only exit readiness score
    is_refuted = active_verification.resulting_claim.trust_level == TrustLevel.AI_PROPOSED_REFUTED
    breakdown = compute_exit_readiness_score(
        baseline=active_verification.baseline,
        migrated=active_verification.migrated,
        differential=active_verification.differential,
        gaps=gaps,
        claim_refuted=is_refuted,
    )

    proposed_pkg = active_verification.differential.replacement_dependency or dep_name

    report = ComparisonReport(
        target_dependency=dep_name,
        proposed_package=proposed_pkg,
        readiness_score=breakdown.total_score,
        breakdown=breakdown,
        gaps=gaps,
        limitations=limitations,
        triages=triages,
        run_dir=str(effective_run_dir),
    )

    # 5. Persist comparison.json in run_dir
    comp_file = effective_run_dir / "comparison.json"
    comp_file.write_text(json.dumps(report.model_dump(mode="json"), indent=2), encoding="utf-8")

    return report
