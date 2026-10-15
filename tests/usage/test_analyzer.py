"""Tests for AST repository usage mapper and blast radius calculation."""

from __future__ import annotations

from pathlib import Path

from exitkit.usage.analyzer import map_repository_usage
from exitkit.usage.models import CriticalPathLevel, UsageKind
from exitkit.util.fingerprint import tree_fingerprint


def test_map_repository_usage_synthetic_repo() -> None:
    """Verify usage analyzer extracts symbols, calls, aliases, and blast radii correctly."""
    repo_dir = Path(__file__).parent.parent / "fixtures" / "synthetic_repo"
    fp_initial = tree_fingerprint(repo_dir)

    report = map_repository_usage(repo_dir)

    # 1. Target immutability check
    fp_final = tree_fingerprint(repo_dir)
    assert fp_initial == fp_final

    # 2. Total source files analyzed
    assert report.total_source_files >= 2
    assert "main.py" in report.source_files

    # 3. Check PyYAML usage mapped from 'import yaml'
    assert "pyyaml" in report.usages
    yaml_usage = report.usages["pyyaml"]
    assert "main.py" in yaml_usage.files_touched
    assert "yaml.safe_load" in yaml_usage.symbols_used
    assert any(s.kind == UsageKind.CALL and s.symbol == "yaml.safe_load" for s in yaml_usage.sites)

    # 4. Check appdirs aliased import 'from appdirs import user_data_dir as udd'
    assert "appdirs" in report.usages
    appdirs_usage = report.usages["appdirs"]
    assert "main.py" in appdirs_usage.files_touched
    assert "appdirs.user_data_dir" in appdirs_usage.symbols_used
    assert any(
        s.kind == UsageKind.CALL and s.symbol == "appdirs.user_data_dir"
        for s in appdirs_usage.sites
    )

    # 5. Check requests multi-file usage across main.py and core/service.py
    assert "requests" in report.usages
    req_usage = report.usages["requests"]
    assert "main.py" in req_usage.files_touched
    assert "core/service.py" in req_usage.files_touched
    assert "requests.get" in req_usage.symbols_used
    assert "requests.Session" in req_usage.symbols_used
    assert "requests.Session.post" in req_usage.symbols_used

    # 6. Check blast radius
    assert req_usage.blast_radius.files_touched_count == 2
    assert req_usage.blast_radius.critical_path_level in (
        CriticalPathLevel.HIGH,
        CriticalPathLevel.CRITICAL,
    )


def test_map_repository_usage_with_dep_filter() -> None:
    """Test filtering usage analysis to a single dependency."""
    repo_dir = Path(__file__).parent.parent / "fixtures" / "synthetic_repo"
    report = map_repository_usage(repo_dir, target_dep="pyyaml")

    assert "pyyaml" in report.usages
    assert "requests" not in report.usages
    assert "appdirs" not in report.usages
