"""CLI integration tests for the exitkit propose command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from exitkit.cli import app

runner = CliRunner()


def test_cli_propose_mock_appdirs(tmp_path: Path) -> None:
    """CLI propose on appdirs using mock backend proposes platformdirs."""
    # Run against local target LinkWatch if present, else fallback to temp repo
    target_repo = Path(".targets/LinkWatch")
    repo_arg = str(target_repo) if target_repo.exists() else str(tmp_path)
    if not target_repo.exists():
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname="demo"\ndependencies=["appdirs"]\n', encoding="utf-8"
        )

    out_dir = tmp_path / "out_appdirs"
    result = runner.invoke(
        app,
        [
            "propose",
            repo_arg,
            "--dep",
            "appdirs",
            "--backend",
            "mock",
            "--out",
            str(out_dir),
        ],
    )

    assert result.exit_code == 0
    assert "platformdirs" in result.stdout
    assert "REPLACE" in result.stdout
    assert "Trust Ledger Receipt" in result.stdout
    assert (out_dir / "proposal.json").exists()


def test_cli_propose_mock_jinja2(tmp_path: Path) -> None:
    """CLI propose on jinja2 using mock backend proposes UPGRADE within Jinja2."""
    target_repo = Path(".targets/LinkWatch")
    repo_arg = str(target_repo) if target_repo.exists() else str(tmp_path)
    if not target_repo.exists():
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname="demo"\ndependencies=["jinja2==2.11.3"]\n', encoding="utf-8"
        )

    out_dir = tmp_path / "out_jinja2"
    result = runner.invoke(
        app,
        [
            "propose",
            repo_arg,
            "--dep",
            "jinja2",
            "--backend",
            "mock",
            "--out",
            str(out_dir),
        ],
    )

    assert result.exit_code == 0
    assert "UPGRADE" in result.stdout
    assert "Jinja2" in result.stdout


def test_cli_propose_json_output(tmp_path: Path) -> None:
    """CLI propose --json prints valid parseable JSON."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="demo"\ndependencies=["appdirs"]\n', encoding="utf-8"
    )

    result = runner.invoke(
        app,
        [
            "propose",
            str(tmp_path),
            "--dep",
            "appdirs",
            "--backend",
            "mock",
            "--json",
        ],
    )

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["target_dependency"] == "appdirs"
    assert data["proposal"]["proposed_package"] == "platformdirs"
    assert data["claim"]["trust_level"] == "AI_PROPOSED"
    assert data["model_info"]["is_mock"] is True


def test_cli_propose_missing_env_vars_fails_cleanly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When no backend or keys are set, propose prints setup instructions and exits with code 1."""
    monkeypatch.delenv("EXITKIT_GEMMA_MODEL", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="demo"\ndependencies=["appdirs"]\n', encoding="utf-8"
    )

    result = runner.invoke(
        app,
        [
            "propose",
            str(tmp_path),
            "--dep",
            "appdirs",
        ],
    )

    assert result.exit_code != 0
    assert "EXITKIT_GEMMA_MODEL" in result.stdout
    assert "How to set up ExitKit Gemma Proposer" in result.stdout


def test_cli_propose_invalid_backend(tmp_path: Path) -> None:
    """Passing an unknown backend is rejected with a clear message."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="demo"\ndependencies=["appdirs"]\n', encoding="utf-8"
    )

    result = runner.invoke(
        app,
        [
            "propose",
            str(tmp_path),
            "--dep",
            "appdirs",
            "--backend",
            "unsupported-backend",
        ],
    )

    assert result.exit_code != 0
    assert "Invalid backend" in result.stdout
