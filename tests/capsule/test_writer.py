"""Tests for capsule bundle generation and archive serialization."""

from __future__ import annotations

import json
import tarfile
from datetime import UTC, datetime
from pathlib import Path

from exitkit.capsule.writer import build_capsule
from exitkit.ledger.models import (
    BaselineOutcome,
    Claim,
    ClaimSource,
    EvidenceKind,
    ModelInfo,
    RunLabel,
    TrustLevel,
)
from exitkit.ledger.store import EvidenceStore
from exitkit.sandbox.models import (
    DifferentialCaseResult,
    DifferentialSuiteResult,
    SandboxVerificationResult,
    TestRunResult,
)

TestRunResult.__test__ = False


def _setup_mock_run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run_123"
    run_dir.mkdir()
    store = EvidenceStore(run_dir)

    # Add evidence files
    ev1 = store.add_text(
        kind=EvidenceKind.TEST_RUN,
        text="All tests passed successfully\n10 passed",
        description="Baseline test run log",
    )
    ev2 = store.add_text(
        kind=EvidenceKind.DIFF,
        text=(
            "--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n"
            "-import appdirs\n+import platformdirs as appdirs\n"
        ),
        description="Unified diff",
        ext="patch",
    )
    ev3 = store.add_text(
        kind=EvidenceKind.TEST_RUN,
        text="Differential test suite executed: 5/5 matched\n",
        description="Differential suite log",
    )

    # Models
    model_info = ModelInfo(
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
        model_info=model_info,
    )
    res_claim = Claim(
        id="claim-res-1",
        statement="Migration verified by differential suite",
        trust_level=TrustLevel.AI_PROPOSED_VERIFIED,
        source=ClaimSource.TOOL,
        evidence_ids=[ev2.id, ev3.id],
        supersedes="claim-prop-1",
    )

    baseline_res = TestRunResult(
        label=RunLabel.BASELINE,
        passed=True,
        exit_code=0,
        tests_ran=10,
        passed_count=10,
        failed_count=0,
        error_count=0,
        skipped_count=0,
        duration_seconds=1.0,
        command=["pytest"],
        stdout="10 passed",
        stderr="",
        baseline_outcome=BaselineOutcome.PASS_AS_DECLARED,
        evidence_id=ev1.id,
    )
    migrated_res = TestRunResult(
        label=RunLabel.MIGRATED,
        passed=True,
        exit_code=0,
        tests_ran=10,
        passed_count=10,
        failed_count=0,
        error_count=0,
        skipped_count=0,
        duration_seconds=1.0,
        command=["pytest"],
        stdout="10 passed",
        stderr="",
        evidence_id=ev1.id,
    )
    diff_case = DifferentialCaseResult(
        name="user_data_dir",
        input_repr="('testapp', 'author')",
        matched=True,
        original_output="/data/testapp",
        replacement_output="/data/testapp",
    )
    diff_suite = DifferentialSuiteResult(
        target_dependency="appdirs",
        replacement_dependency="platformdirs",
        total_cases=1,
        matched_cases=1,
        duration_seconds=0.5,
        all_matched=True,
        cases=[diff_case],
        evidence_id=ev3.id,
    )

    verif = SandboxVerificationResult(
        target_dependency="appdirs",
        target_repo=str(tmp_path),
        python_version="3.12",
        baseline=baseline_res,
        migrated=migrated_res,
        differential=diff_suite,
        unified_patch="--- a/file.py\n+++ b/file.py\n",
        patch_evidence_id=ev2.id,
        proposal_claim=prop_claim,
        resulting_claim=res_claim,
        verified=True,
        evidence_items=[ev1, ev2, ev3],
        run_dir=str(run_dir),
    )

    v_file = run_dir / "verification.json"
    v_file.write_text(json.dumps(verif.model_dump(mode="json"), indent=2), encoding="utf-8")
    return run_dir


def test_build_capsule_directory_bundle(tmp_path: Path) -> None:
    """build_capsule produces a valid directory bundle with manifest and receipts."""
    run_dir = _setup_mock_run_dir(tmp_path)
    capsule_out = tmp_path / "capsule_output"

    built_path = build_capsule(run_dir=run_dir, output_dir=capsule_out, archive=False)

    assert built_path.is_dir()
    assert (built_path / "capsule.json").exists()
    assert (built_path / "SHA256SUMS").exists()
    assert (built_path / "report.html").exists()
    assert (built_path / "patch" / "migration.patch").exists()
    assert (built_path / "evidence").is_dir()

    manifest_text = (built_path / "SHA256SUMS").read_text(encoding="utf-8")
    assert "capsule.json" in manifest_text
    assert "patch/migration.patch" in manifest_text
    assert "report.html" in manifest_text


def test_build_capsule_tar_archive(tmp_path: Path) -> None:
    """build_capsule with archive=True produces a compressed .tar.gz bundle."""
    run_dir = _setup_mock_run_dir(tmp_path)
    capsule_out = tmp_path / "capsule_bundle.tar.gz"

    archive_path = build_capsule(run_dir=run_dir, output_dir=capsule_out, archive=True)

    assert archive_path.is_file()
    assert str(archive_path).endswith(".tar.gz")

    with tarfile.open(archive_path, "r:gz") as tar:
        names = tar.getnames()
        assert "capsule.json" in names
        assert "SHA256SUMS" in names
        assert "report.html" in names
        assert "patch/migration.patch" in names
