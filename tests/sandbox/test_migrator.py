"""Tests for applying migration proposals and generating unified diffs."""

from __future__ import annotations

from pathlib import Path

from exitkit.ledger.models import ProposalKind
from exitkit.llm.models import MigrationProposal
from exitkit.sandbox.migrator import apply_migration


def test_apply_migration_appdirs_replace(tmp_path: Path) -> None:
    """apply_migration replaces appdirs in manifests and source code."""
    ws = tmp_path / "app"
    ws.mkdir()

    # Manifest
    pyproject = ws / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "test"\ndependencies = [\n    "appdirs>=1.4.0",\n]\n',
        encoding="utf-8",
    )
    reqs = ws / "requirements.txt"
    reqs.write_text("appdirs==1.4.4\nrequests>=2.28\n", encoding="utf-8")

    # Source files
    src = ws / "src"
    src.mkdir()
    code_file = src / "cache.py"
    code_file.write_text(
        "import appdirs\nfrom appdirs import user_cache_dir\n\npath = user_cache_dir('app')\n",
        encoding="utf-8",
    )

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

    unified_patch, modified = apply_migration(ws, proposal)

    assert len(modified) == 3
    assert "platformdirs>=4.0.0" in pyproject.read_text(encoding="utf-8")
    assert "platformdirs>=4.0.0" in reqs.read_text(encoding="utf-8")
    assert "import platformdirs" in code_file.read_text(encoding="utf-8")
    assert "from platformdirs import user_cache_dir" in code_file.read_text(encoding="utf-8")

    # Unified diff was generated
    assert "--- a/pyproject.toml" in unified_patch
    assert '+    "platformdirs>=4.0.0",' in unified_patch
    assert "--- a/src/cache.py" in unified_patch
    assert "+import platformdirs" in unified_patch


def test_apply_migration_jinja2_upgrade(tmp_path: Path) -> None:
    """apply_migration upgrades Jinja2 constraint without altering imports."""
    ws = tmp_path / "app"
    ws.mkdir()

    reqs = ws / "requirements.txt"
    reqs.write_text("Jinja2==2.11.3\n", encoding="utf-8")

    code_file = ws / "report.py"
    code_file.write_text("import jinja2\n", encoding="utf-8")

    proposal = MigrationProposal(
        target_dependency="jinja2",
        proposed_package="Jinja2",
        proposed_specifier=">=3.0.0",
        kind=ProposalKind.UPGRADE,
        rationale="Upgrade Jinja2 for Python 3.12 compatibility.",
        migration_steps=["Upgrade Jinja2==2.11.3 to Jinja2>=3.0.0"],
        confidence=0.98,
        expected_shim_needed=False,
    )

    unified_patch, modified = apply_migration(ws, proposal)

    assert len(modified) == 1
    assert "Jinja2>=3.0.0" in reqs.read_text(encoding="utf-8")
    assert "import jinja2" in code_file.read_text(encoding="utf-8")
    assert "--- a/requirements.txt" in unified_patch
