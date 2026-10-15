"""Tests for the differential testing engine."""

from __future__ import annotations

import sys
from pathlib import Path

from exitkit.ledger.models import ProposalKind
from exitkit.ledger.store import EvidenceStore
from exitkit.llm.models import MigrationProposal
from exitkit.sandbox.differential import run_differential_suite


def test_differential_suite_appdirs_vs_platformdirs(tmp_path: Path) -> None:
    """Differential suite asserts parity for appdirs -> platformdirs."""
    run_dir = tmp_path / "diff_run"
    store = EvidenceStore(run_dir)

    proposal = MigrationProposal(
        target_dependency="appdirs",
        proposed_package="platformdirs",
        proposed_specifier=">=4.0.0",
        kind=ProposalKind.REPLACE,
        rationale="Replace unmaintained appdirs with platformdirs.",
        migration_steps=["Replace appdirs with platformdirs"],
        confidence=0.95,
        expected_shim_needed=False,
    )

    result = run_differential_suite(
        dep_name="appdirs",
        proposal=proposal,
        python_bin=Path(sys.executable),
        store=store,
    )

    assert result.total_cases > 0
    assert result.matched_cases > 0
    assert result.all_matched is True
    assert store.get(result.evidence_id) is not None


def test_differential_suite_jinja2(tmp_path: Path) -> None:
    """Differential suite asserts parity for Jinja2 template rendering."""
    run_dir = tmp_path / "jinja2_diff"
    store = EvidenceStore(run_dir)

    proposal = MigrationProposal(
        target_dependency="jinja2",
        proposed_package="Jinja2",
        proposed_specifier=">=3.0.0",
        kind=ProposalKind.UPGRADE,
        rationale="Upgrade Jinja2.",
        migration_steps=["Upgrade Jinja2"],
        confidence=0.98,
        expected_shim_needed=False,
    )

    result = run_differential_suite(
        dep_name="jinja2",
        proposal=proposal,
        python_bin=Path(sys.executable),
        store=store,
    )

    assert result.total_cases >= 4
    assert result.all_matched is True
    assert result.matched_cases == result.total_cases
