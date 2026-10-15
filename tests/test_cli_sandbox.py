"""CLI integration tests for the exitkit sandbox command."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from exitkit.cli import app

runner = CliRunner()


def test_cli_sandbox_json_output(tmp_path: Path) -> None:
    """CLI sandbox --json produces valid JSON verification structure."""
    target_repo = Path(".targets/LinkWatch")
    repo_arg = str(target_repo) if target_repo.exists() else str(tmp_path)
    if not target_repo.exists():
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname="demo"\ndependencies=["appdirs"]\n', encoding="utf-8"
        )

    out_dir = tmp_path / "sandbox_out"
    result = runner.invoke(
        app,
        [
            "sandbox",
            repo_arg,
            "--dep",
            "appdirs",
            "--backend",
            "mock",
            "--json",
            "--out",
            str(out_dir),
        ],
    )

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["target_dependency"] == "appdirs"
    assert "baseline" in data
    assert "migrated" in data
    assert "differential" in data
    assert "resulting_claim" in data
    assert data["verified"] is True
    assert (out_dir / "verification.json").exists()


def test_cli_sandbox_invalid_backend(tmp_path: Path) -> None:
    """Invalid backend option is rejected with a message."""
    result = runner.invoke(
        app,
        [
            "sandbox",
            str(tmp_path),
            "--dep",
            "appdirs",
            "--backend",
            "unknown-backend",
        ],
    )

    assert result.exit_code != 0
    assert "Invalid backend" in result.stdout
