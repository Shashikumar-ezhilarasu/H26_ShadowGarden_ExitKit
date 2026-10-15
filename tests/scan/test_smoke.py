"""Unit tests for smoke testing runner and resolver dry-runs."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from exitkit.ledger.models import SignalStatus
from exitkit.ledger.store import EvidenceStore
from exitkit.scan.smoke import (
    resolve_import_name,
    run_smoke_test,
)


def test_resolve_import_name_mappings() -> None:
    """Verify fallback and known package-to-import mappings."""
    assert resolve_import_name("PyYAML") == "yaml"
    assert resolve_import_name("Jinja2") == "jinja2"
    assert resolve_import_name("python-dateutil") == "dateutil"
    assert resolve_import_name("appdirs") == "appdirs"
    assert resolve_import_name("pydantic") == "pydantic"
    assert resolve_import_name("unknown-lib") == "unknown_lib"


def test_smoke_test_runner_isolation_and_command_building(tmp_path: Path) -> None:
    """Verify smoke test creates venv in temp dir and runs isolated import check."""
    store = EvidenceStore(tmp_path)

    # Mock subprocess.run to simulate successful venv creation and import
    def mock_subprocess_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        cmd_str = " ".join(cmd)
        cwd_val = str(kwargs.get("cwd", ""))

        # Must never run inside target repository
        assert "target_repo" not in cwd_val
        assert "exitkit_smoke" in cwd_val or tmp_path.as_posix() in cwd_val

        if "uv venv" in cmd_str:
            # Create dummy python binary in the mocked venv path
            venv_dir = Path(cmd[2])
            bin_dir = venv_dir / "bin"
            bin_dir.mkdir(parents=True, exist_ok=True)
            (bin_dir / "python").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="venv created", stderr="")

        if "uv pip install" in cmd_str:
            return subprocess.CompletedProcess(cmd, 0, stdout="Successfully installed", stderr="")

        if "importlib.metadata" in cmd_str:
            return subprocess.CompletedProcess(cmd, 0, stdout="2.11.3\n", stderr="")

        if "-c import jinja2" in cmd_str:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("exitkit.scan.smoke.subprocess.run", side_effect=mock_subprocess_run),
        patch("exitkit.scan.smoke.is_python_available", return_value=True),
    ):
        result = run_smoke_test("Jinja2", "==2.11.3", "3.12", store)

    assert result.status == SignalStatus.AVAILABLE
    assert result.install_passed
    assert result.import_passed
    assert result.installed_version == "2.11.3"
    assert result.install_evidence is not None
    assert result.import_evidence is not None


def test_smoke_test_runner_captures_import_failure(tmp_path: Path) -> None:
    """Verify import failure (such as soft_unicode) is captured in evidence."""
    store = EvidenceStore(tmp_path)

    def mock_fail_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        cmd_str = " ".join(cmd)
        if "uv venv" in cmd_str:
            venv_dir = Path(cmd[2])
            bin_dir = venv_dir / "bin"
            bin_dir.mkdir(parents=True, exist_ok=True)
            (bin_dir / "python").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        if "uv pip install" in cmd_str:
            return subprocess.CompletedProcess(cmd, 0, stdout="Installed", stderr="")

        if "-c import jinja2" in cmd_str:
            err = (
                "Traceback (most recent call last):\n"
                "  File '<string>', line 1, in <module>\n"
                "ImportError: cannot import name 'soft_unicode' from 'markupsafe'"
            )
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=err)

        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("exitkit.scan.smoke.subprocess.run", side_effect=mock_fail_run),
        patch("exitkit.scan.smoke.is_python_available", return_value=True),
    ):
        result = run_smoke_test("Jinja2", "==2.11.3", "3.12", store)

    assert result.status == SignalStatus.AVAILABLE
    assert result.install_passed
    assert not result.import_passed
    assert "ImportError: cannot import name 'soft_unicode'" in (result.error_summary or "")
    assert result.import_evidence is not None
