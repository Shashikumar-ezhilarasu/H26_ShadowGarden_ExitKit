"""Tests for SandboxRunner timeout enforcement and test execution."""

from __future__ import annotations

import sys
from pathlib import Path

from exitkit.ledger.models import BaselineOutcome, RunLabel
from exitkit.ledger.store import EvidenceStore
from exitkit.sandbox.runner import SandboxRunner


def test_sandbox_runner_timeout_enforcement(tmp_path: Path) -> None:
    """Commands exceeding timeout are terminated with exit_code=-9 and timeout_hit=True."""
    runner = SandboxRunner(workspace=tmp_path, timeout=1.0)
    # Run sleep command longer than timeout
    res = runner.run_command(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        timeout=0.5,
    )

    assert res.timeout_hit is True
    assert res.exit_code == -9
    assert "timed out after" in res.stderr


def test_sandbox_runner_no_tests_detected(tmp_path: Path) -> None:
    """When no test files exist in workspace, NO_TESTS baseline outcome is recorded."""
    run_dir = tmp_path / "run"
    store = EvidenceStore(run_dir)

    runner = SandboxRunner(workspace=tmp_path)
    res = runner.run_test_suite(label=RunLabel.BASELINE, store=store)

    assert res.passed is True
    assert res.baseline_outcome == BaselineOutcome.NO_TESTS
    assert res.tests_ran == 0
    assert store.get(res.evidence_id) is not None
