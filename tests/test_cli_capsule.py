"""CLI integration tests for exitkit capsule and exitkit verify."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from exitkit.cli import app
from tests.capsule.test_writer import _setup_mock_run_dir

runner = CliRunner()


def test_cli_capsule_and_verify_lifecycle(tmp_path: Path) -> None:
    """End-to-end CLI workflow: create capsule -> verify capsule."""
    run_dir = _setup_mock_run_dir(tmp_path)
    capsule_out = tmp_path / "cli_capsule"

    # 1. Create capsule
    res = runner.invoke(
        app,
        [
            "capsule",
            str(run_dir),
            "--output",
            str(capsule_out),
        ],
    )
    assert res.exit_code == 0, res.stdout
    assert "Dependency Exit Capsule Created" in res.stdout

    # 2. Verify capsule
    verify_res = runner.invoke(app, ["verify", str(capsule_out)])
    assert verify_res.exit_code == 0, verify_res.stdout
    assert "VERIFIED" in verify_res.stdout


def test_cli_capsule_json_output(tmp_path: Path) -> None:
    """exitkit capsule --json outputs clean machine-readable JSON."""
    run_dir = _setup_mock_run_dir(tmp_path)
    capsule_out = tmp_path / "json_capsule"

    res = runner.invoke(
        app,
        [
            "capsule",
            str(run_dir),
            "--output",
            str(capsule_out),
            "--json",
        ],
    )
    assert res.exit_code == 0, res.stdout
    data = json.loads(res.stdout)
    assert "capsule_path" in data
    assert data["format"] == "dir"


def test_cli_verify_tampered_fails(tmp_path: Path) -> None:
    """exitkit verify exits with code 1 when tamper is detected."""
    run_dir = _setup_mock_run_dir(tmp_path)
    capsule_out = tmp_path / "tampered_cli_capsule"

    runner.invoke(app, ["capsule", str(run_dir), "--output", str(capsule_out)])

    # Tamper with an evidence file
    ev_file = next((capsule_out / "evidence").iterdir())
    ev_file.chmod(0o644)
    ev_file.write_bytes(b"corrupted data")

    res = runner.invoke(app, ["verify", str(capsule_out)])
    assert res.exit_code == 1
    assert "TAMPERED" in res.stdout


def test_cli_verify_json_output(tmp_path: Path) -> None:
    """exitkit verify --json prints valid structured JSON verification result."""
    run_dir = _setup_mock_run_dir(tmp_path)
    capsule_out = tmp_path / "verify_json_capsule"

    runner.invoke(app, ["capsule", str(run_dir), "--output", str(capsule_out)])

    res = runner.invoke(app, ["verify", str(capsule_out), "--json"])
    assert res.exit_code == 0, res.stdout
    data = json.loads(res.stdout)
    assert data["status"] == "VERIFIED"
    assert data["is_valid"] is True
