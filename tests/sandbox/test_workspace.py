"""Tests for isolated sandbox workspace and original repository immutability (Rule 5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from exitkit.sandbox.workspace import isolated_workspace
from exitkit.util.fingerprint import tree_fingerprint


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    """Create a sample target repository."""
    repo = tmp_path / "sample_repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")
    pkg = repo / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("VALUE = 42\n", encoding="utf-8")
    return repo


def test_isolated_workspace_preserves_original_immutability(sample_repo: Path) -> None:
    """Sandbox operations do not alter original target files (Rule 5)."""
    fp_initial = tree_fingerprint(sample_repo)

    with isolated_workspace(sample_repo) as ws:
        assert ws.exists()
        assert ws != sample_repo

        # Mutate files inside sandbox workspace
        (ws / "pyproject.toml").write_text('[project]\nname = "mutated"\n', encoding="utf-8")
        (ws / "new_file.txt").write_text("temporary data\n", encoding="utf-8")
        assert (ws / "new_file.txt").exists()

    # Workspace is cleaned up
    assert not ws.exists()

    # Original repo fingerprint is exactly identical
    fp_after = tree_fingerprint(sample_repo)
    assert fp_after == fp_initial


def test_isolated_workspace_detects_original_tampering(sample_repo: Path) -> None:
    """If original repository is tampered during execution, an error is raised (Rule 5)."""
    with pytest.raises(RuntimeError) as exc_info:
        with isolated_workspace(sample_repo):
            # Tamper with original repo outside workspace
            (sample_repo / "tampered.txt").write_text("illegal modification\n", encoding="utf-8")

    assert "Target repository safety violation" in str(exc_info.value)
