"""Unit tests for manifest discovery, normalization, and divergence detection."""

from __future__ import annotations

from pathlib import Path

from exitkit.discover.manifests import (
    _check_specifiers_compatible,
    discover_dependencies,
    parse_lockfiles,
    parse_pyproject_toml,
    parse_requirements_file,
    parse_setup_cfg,
    parse_setup_py,
)


def test_parse_pyproject_toml_variants(tmp_path: Path) -> None:
    """Parse standard PEP 621, optional-dependencies, dependency-groups, and poetry manifests."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        """
[project]
name = "demo"
version = "0.1.0"
dependencies = [
    "requests>=2.28",
    "pydantic>=2.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
]

[dependency-groups]
test = [
    "pytest-mock>=3.0",
]

[tool.poetry.dependencies]
rich = "^13.0"
python = ">=3.12"
""",
        encoding="utf-8",
    )

    deps = parse_pyproject_toml(pyproject, tmp_path)
    names = {d.canonical_name for d in deps}

    assert "requests" in names
    assert "pydantic" in names
    assert "pytest" in names
    assert "pytest-mock" in names
    assert "rich" in names
    assert "python" not in names  # python runtime constraint excluded


def test_parse_requirements_file_advanced(tmp_path: Path) -> None:
    """Parse requirements with -r includes, markers, extras, hashes, comments, and VCS lines."""
    base_req = tmp_path / "requirements.txt"
    sub_req = tmp_path / "sub_requirements.txt"

    sub_req.write_text(
        """
click>=8.0
# Comment inside sub-requirement
# flask==2.0.1
""",
        encoding="utf-8",
    )

    base_req.write_text(
        """
--index-url https://pypi.org/simple
-r sub_requirements.txt
requests[security]>=2.25.0; python_version >= '3.10'
cryptography==42.0.0 --hash=sha256:abcdef1234567890
git+https://github.com/psf/black.git@main
-e ./local_package
# PyYAML==5.1
# Normal comment
""",
        encoding="utf-8",
    )

    deps, commented_pins = parse_requirements_file(base_req, tmp_path)
    cnames = {d.canonical_name for d in deps}

    assert "requests" in cnames
    assert "click" in cnames
    assert "cryptography" in cnames
    assert "black" in cnames
    assert any("PyYAML==5.1" in p for p in commented_pins)
    assert any("flask==2.0.1" in p for p in commented_pins)

    # Verify non-pypi flags
    black_dep = next(d for d in deps if d.canonical_name == "black")
    assert not black_dep.is_pypi


def test_parse_setup_cfg(tmp_path: Path) -> None:
    """Parse install_requires from setup.cfg."""
    setup_cfg = tmp_path / "setup.cfg"
    setup_cfg.write_text(
        """
[metadata]
name = pkg

[options]
install_requires =
    toml==0.10.2
    appdirs>=1.4.0
""",
        encoding="utf-8",
    )

    deps = parse_setup_cfg(setup_cfg, tmp_path)
    cnames = {d.canonical_name for d in deps}
    assert "toml" in cnames
    assert "appdirs" in cnames


def test_parse_setup_py_literal_and_dynamic(tmp_path: Path) -> None:
    """Parse setup.py AST for literal install_requires and flag dynamic expressions."""
    setup_lit = tmp_path / "setup_lit.py"
    setup_lit.write_text(
        """
from setuptools import setup

setup(
    name="lit_pkg",
    install_requires=[
        "Jinja2==2.11.3",
        "markupsafe>=2.0",
    ]
)
""",
        encoding="utf-8",
    )

    deps, limits = parse_setup_py(setup_lit, tmp_path)
    assert {d.canonical_name for d in deps} == {"jinja2", "markupsafe"}
    assert len(limits) == 0

    setup_dyn = tmp_path / "setup_dyn.py"
    setup_dyn.write_text(
        """
from setuptools import setup

def get_reqs():
    return ["requests"]

setup(
    name="dyn_pkg",
    install_requires=get_reqs(),
)
""",
        encoding="utf-8",
    )

    dyn_deps, dyn_limits = parse_setup_py(setup_dyn, tmp_path)
    assert len(dyn_deps) == 0
    assert any("Dynamic install_requires" in lim for lim in dyn_limits)


def test_parse_lockfiles(tmp_path: Path) -> None:
    """Parse resolved package versions from uv.lock."""
    uv_lock = tmp_path / "uv.lock"
    uv_lock.write_text(
        """
[[package]]
name = "jinja2"
version = "3.1.4"

[[package]]
name = "pydantic"
version = "2.7.1"
""",
        encoding="utf-8",
    )

    resolved = parse_lockfiles(tmp_path)
    assert resolved.get("jinja2") == "3.1.4"
    assert resolved.get("pydantic") == "2.7.1"


def test_specifier_compatibility_and_divergence() -> None:
    """Test compatibility logic on conflicting vs compatible specifier ranges."""
    # Incompatible pin vs range (LinkWatch pydantic case)
    compat, detail = _check_specifiers_compatible([">=2", "==1.10.13"])
    assert not compat
    assert "conflicts" in detail

    # Incompatible disjoint ranges
    compat_disjoint, _ = _check_specifiers_compatible(["<1.0.0", ">=2.0.0"])
    assert not compat_disjoint

    # Compatible overlapping ranges
    compat_ok, _ = _check_specifiers_compatible([">=1.0.0", ">=1.5.0", "<3.0.0"])
    assert compat_ok


def test_discover_dependencies_end_to_end(tmp_path: Path) -> None:
    """Verify discover_dependencies aggregates, normalizes, and identifies divergence."""
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "full_test"
dependencies = [
    "pydantic>=2",
    "requests==2.31.0",
]
""",
        encoding="utf-8",
    )

    (tmp_path / "requirements.txt").write_text(
        """
pydantic==1.10.13
requests==2.31.0
six
# PyYAML==5.1
""",
        encoding="utf-8",
    )

    result = discover_dependencies(tmp_path)
    dep_dict = {d.canonical_name: d for d in result.dependencies}

    assert "pydantic" in dep_dict
    assert "requests" in dep_dict
    assert "six" in dep_dict

    # Check divergence for pydantic
    pyd_div = dep_dict["pydantic"].divergence
    assert pyd_div is not None
    assert not pyd_div.compatible

    # Requests has identical specifiers across files, so no divergence
    req_div = dep_dict["requests"].divergence
    assert req_div is None

    # Check commented pin recorded
    assert any("PyYAML==5.1" in pin for pin in result.commented_out_pins)
