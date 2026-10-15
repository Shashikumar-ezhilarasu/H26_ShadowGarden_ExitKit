"""CLI integration tests for exitkit compare."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from exitkit.cli import app

runner = CliRunner()


def test_cli_compare_mock(tmp_path: Path) -> None:
    """exitkit compare on a synthetic or fixture directory with mock verification."""
    # Create minimal pyproject.toml
    proj_dir = tmp_path / "mock_project"
    proj_dir.mkdir()
    (proj_dir / "pyproject.toml").write_text(
        """[project]
name = "mock-proj"
version = "0.1.0"
dependencies = [
    "appdirs>=1.4.0",
]
""",
        encoding="utf-8",
    )

    # Run compare with mock backend
    res = runner.invoke(
        app,
        [
            "compare",
            str(proj_dir),
            "--dep",
            "appdirs",
            "--backend",
            "mock",
            "--json",
        ],
    )

    assert res.exit_code == 0, res.stdout
    data = json.loads(res.stdout)
    assert data["target_dependency"] == "appdirs"
    assert "readiness_score" in data
    assert "breakdown" in data
    assert "gaps" in data
    assert "limitations" in data


def test_cli_compare_invalid_repo() -> None:
    """Non-existent repo path returns exit code 1."""
    res = runner.invoke(app, ["compare", "/non/existent/path/999", "--dep", "foo"])
    assert res.exit_code == 1
    assert "does not exist" in res.stdout
