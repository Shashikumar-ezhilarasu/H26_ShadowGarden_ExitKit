"""Tests for scanner target safety, tree immutability, and ledger integrity verification."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from exitkit.ledger.models import EvidenceKind, SignalStatus
from exitkit.scan.scanner import run_scan
from exitkit.util.fingerprint import tree_fingerprint
from tests.scan.test_clients import create_mock_transport


def test_scanner_preserves_target_tree_immutability(tmp_path: Path) -> None:
    """Verify target repo tree fingerprint is strictly preserved and no files
    are created inside target.
    """
    target_repo = tmp_path / "mock_target"
    target_repo.mkdir()

    # Create dummy target repo files
    (target_repo / "pyproject.toml").write_text(
        """
[project]
name = "safety_test"
version = "0.1.0"
dependencies = [
    "jinja2==2.11.3",
]
""",
        encoding="utf-8",
    )
    (target_repo / "main.py").write_text("import jinja2\n", encoding="utf-8")

    fp_initial = tree_fingerprint(target_repo)
    transport = create_mock_transport()

    # Mock smoke test to avoid calling external subprocess in safety test
    from exitkit.scan.smoke import SmokeResult

    def dummy_smoke(name: str, spec: str, py_ver: str, store: object, **kw: object) -> SmokeResult:
        ev = store.add_text(EvidenceKind.COMMAND_OUTPUT, "Mock smoke log", "Mock run")
        return SmokeResult(
            status=SignalStatus.AVAILABLE,
            python_version=py_ver,
            install_passed=True,
            import_passed=True,
            installed_version="2.11.3",
            install_evidence=ev,
            import_evidence=ev,
        )

    out_dir = tmp_path / "run_out"
    with (
        patch("exitkit.scan.scanner.run_smoke_test", side_effect=dummy_smoke),
        patch(
            "exitkit.scan.scanner.run_repo_dry_run_resolver",
            return_value=(SignalStatus.AVAILABLE, True, None, None),
        ),
    ):
        result = run_scan(
            repo_path_or_url=str(target_repo),
            use_cache=False,
            out_dir=out_dir,
            http_transport=transport,
        )

    # 1. Fingerprint must match before and after
    fp_after = tree_fingerprint(target_repo)
    assert fp_initial == fp_after
    assert result.tree_fingerprint == fp_initial

    # 2. Check no files created inside target
    assert not (target_repo / "uv.lock").exists()
    assert not (target_repo / ".venv").exists()
    assert not (target_repo / ".exitkit").exists()

    # 3. All stored evidence cryptographically verified
    assert len(result.verification_problems) == 0
    assert result.evidence_count > 0

    # 4. scan.json generated in run directory
    assert (out_dir / "scan.json").exists()
