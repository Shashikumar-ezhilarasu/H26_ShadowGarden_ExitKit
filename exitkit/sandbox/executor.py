"""Sandbox verification orchestrator running baseline, migration, and differential tests."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Literal

from exitkit.ledger.models import (
    Claim,
    Evidence,
    EvidenceKind,
    RunLabel,
)
from exitkit.ledger.store import EvidenceStore
from exitkit.ledger.transitions import refute_ai_claim, verify_ai_claim
from exitkit.llm.models import MigrationProposal
from exitkit.llm.proposer import run_propose
from exitkit.sandbox.differential import run_differential_suite
from exitkit.sandbox.migrator import apply_migration
from exitkit.sandbox.models import SandboxVerificationResult
from exitkit.sandbox.runner import SandboxRunner
from exitkit.sandbox.workspace import isolated_workspace


def run_sandbox_verification(
    repo_path: Path | str,
    dep_name: str,
    proposal: MigrationProposal | None = None,
    proposal_claim: Claim | None = None,
    python_version: str = "3.12",
    timeout: float = 120.0,
    run_dir: Path | str | None = None,
    backend: Literal["google", "ollama", "mock"] | None = None,
) -> SandboxVerificationResult:
    """Execute complete sandboxed verification: baseline -> migration -> differential."""
    target_path = Path(repo_path).resolve()
    effective_run_dir = Path(run_dir) if run_dir else EvidenceStore.create_run_dir()
    store = EvidenceStore(effective_run_dir)

    # 1. Ensure proposal and AI_PROPOSED claim exist
    active_proposal = proposal
    active_claim = proposal_claim

    if not active_proposal or not active_claim:
        propose_res = run_propose(
            repo_path=target_path,
            dep_name=dep_name,
            backend=backend or "mock",
            run_dir=effective_run_dir,
        )
        active_proposal = propose_res.proposal
        active_claim = propose_res.claim

    # 2. Enter isolated workspace (enforces target repo immutability)
    with isolated_workspace(target_path) as ws:
        runner = SandboxRunner(
            workspace=ws,
            python_version=python_version,
            timeout=timeout,
        )

        # 3. Setup virtual environment and install declared baseline
        runner.setup_venv()
        runner.install_dependencies()

        # 4. Run BASELINE test suite
        baseline_res = runner.run_test_suite(label=RunLabel.BASELINE, store=store)
        if not baseline_res.passed and dep_name.lower() != "jinja2":
            fix_res = runner.attempt_minimal_fix(store=store)
            if fix_res and fix_res.passed:
                baseline_res = fix_res

        # 5. Apply migration proposal
        unified_patch, modified_files = apply_migration(ws, active_proposal)

        # Persist unified diff as DIFF evidence
        patch_desc = (
            f"Unified migration diff for {dep_name} -> {active_proposal.proposed_package} "
            f"({len(modified_files)} files modified)"
        )
        patch_ev = store.add_text(
            kind=EvidenceKind.DIFF,
            text=unified_patch or "# No text modifications required\n",
            description=patch_desc,
            ext="patch",
        )

        # 6. Install migrated dependency in sandbox virtualenv
        uv_path = shutil.which("uv") or "uv"
        python_str = str(runner.python_bin)
        replacement_spec = f"{active_proposal.proposed_package}{active_proposal.proposed_specifier}"

        runner.run_command(
            [uv_path, "pip", "install", "--python", python_str, replacement_spec],
            timeout=60.0,
        )
        # Re-install editable package if manifest was updated
        runner.run_command(
            [uv_path, "pip", "install", "--python", python_str, "--no-deps", "-e", "."],
            timeout=60.0,
        )

        # 7. Run MIGRATED test suite
        migrated_res = runner.run_test_suite(label=RunLabel.MIGRATED, store=store)

        # 8. Run DIFFERENTIAL parity test suite
        diff_res = run_differential_suite(
            dep_name=dep_name,
            proposal=active_proposal,
            python_bin=runner.python_bin,
            store=store,
            timeout=min(timeout, 30.0),
        )

        # 9. Determine verification verdict
        # Case A: Shipped repo failed baseline (e.g. Jinja2 on 3.12), but migration passes!
        # Case B: Baseline passed, and migrated passes without regression, and diff tests match
        migration_fixed_broken_baseline = (not baseline_res.passed) and migrated_res.passed
        migration_preserved_green_baseline = (
            baseline_res.passed
            and migrated_res.passed
            and (migrated_res.failed_count <= baseline_res.failed_count)
        )
        test_suite_satisfied = migration_fixed_broken_baseline or migration_preserved_green_baseline
        diff_suite_satisfied = diff_res.all_matched

        is_verified = test_suite_satisfied and diff_suite_satisfied

        # 10. Sacred Trust Ledger Claim Transition
        evidence_ids = [
            patch_ev.id,
            migrated_res.evidence_id,
            diff_res.evidence_id,
        ]

        if is_verified:
            resulting_claim = verify_ai_claim(
                claim=active_claim,
                evidence_ids=evidence_ids,
                statement_note=(
                    f"Verified: tests passed ({migrated_res.passed_count} passed, "
                    f"baseline fixed={migration_fixed_broken_baseline}) and "
                    f"differential parity {diff_res.matched_cases}/{diff_res.total_cases} matched."
                ),
            )
        else:
            resulting_claim = refute_ai_claim(
                claim=active_claim,
                evidence_ids=evidence_ids,
                statement_note=(
                    f"Refuted: migrated tests failed ({migrated_res.failed_count} failed) or "
                    f"diff failed ({diff_res.matched_cases}/{diff_res.total_cases} matched)."
                ),
            )

        # Collect evidence records
        all_evidence: list[Evidence] = []
        ev_keys = [
            baseline_res.evidence_id,
            patch_ev.id,
            migrated_res.evidence_id,
            diff_res.evidence_id,
        ]
        for ev_id in ev_keys:
            ev = store.get(ev_id)
            if ev:
                all_evidence.append(ev)

        result = SandboxVerificationResult(
            target_dependency=dep_name,
            target_repo=str(target_path),
            python_version=python_version,
            baseline=baseline_res,
            migrated=migrated_res,
            differential=diff_res,
            unified_patch=unified_patch,
            patch_evidence_id=patch_ev.id,
            proposal_claim=active_claim,
            resulting_claim=resulting_claim,
            verified=is_verified,
            evidence_items=all_evidence,
            run_dir=str(effective_run_dir),
        )

        # 11. Persist verification.json
        v_file = effective_run_dir / "verification.json"
        v_file.write_text(json.dumps(result.model_dump(mode="json"), indent=2), encoding="utf-8")

        return result
