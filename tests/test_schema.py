"""Tests for schema CLI command and docs/capsule.schema.json export."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from exitkit.cli import app

runner = CliRunner()


def test_schema_command_prints_valid_json() -> None:
    """exitkit schema prints valid JSON Schema to stdout."""
    result = runner.invoke(app, ["schema"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed.get("title") == "Capsule"
    assert "properties" in parsed
    assert "claims" in parsed["properties"]
    assert "evidence" in parsed["properties"]


def test_schema_matches_committed_docs_schema() -> None:
    """The output of exitkit schema matches docs/capsule.schema.json."""
    schema_file = Path("docs/capsule.schema.json")
    assert schema_file.exists(), "docs/capsule.schema.json must be committed"

    result = runner.invoke(app, ["schema"])
    assert result.exit_code == 0

    stdout_schema = json.loads(result.output)
    file_schema = json.loads(schema_file.read_text(encoding="utf-8"))

    assert stdout_schema == file_schema
