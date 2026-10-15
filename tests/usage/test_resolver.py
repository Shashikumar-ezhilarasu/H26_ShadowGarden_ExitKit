"""Tests for import-to-distribution resolution logic."""

from __future__ import annotations

from exitkit.usage.resolver import (
    is_stdlib_module,
    resolve_dist_to_modules,
    resolve_module_to_dist,
)


def test_is_stdlib_module() -> None:
    """Verify standard library modules are correctly identified."""
    assert is_stdlib_module("os")
    assert is_stdlib_module("sys")
    assert is_stdlib_module("json")
    assert is_stdlib_module("pathlib")
    assert is_stdlib_module("typing")
    assert is_stdlib_module("ast")

    assert not is_stdlib_module("requests")
    assert not is_stdlib_module("jinja2")
    assert not is_stdlib_module("yaml")
    assert not is_stdlib_module("appdirs")
    assert not is_stdlib_module("toml")


def test_resolve_module_to_dist_known_mappings() -> None:
    """Test resolution of known package and module discrepancies."""
    # Discrepancy cases
    assert resolve_module_to_dist("yaml") == "pyyaml"
    assert resolve_module_to_dist("dateutil") == "python-dateutil"
    assert resolve_module_to_dist("PIL") == "pillow"
    assert resolve_module_to_dist("bs4") == "beautifulsoup4"
    assert resolve_module_to_dist("dotenv") == "python-dotenv"
    assert resolve_module_to_dist("cv2") == "opencv-python"
    assert resolve_module_to_dist("pkg_resources") == "setuptools"

    # Standard library modules should return None
    assert resolve_module_to_dist("sys") is None
    assert resolve_module_to_dist("os") is None

    # Identical module and dist names
    assert resolve_module_to_dist("requests") == "requests"
    assert resolve_module_to_dist("click") == "click"
    assert resolve_module_to_dist("appdirs") == "appdirs"
    assert resolve_module_to_dist("toml") == "toml"


def test_resolve_dist_to_modules() -> None:
    """Test mapping canonical distribution names back to top-level import names."""
    assert resolve_dist_to_modules("pyyaml") == ["yaml"]
    assert resolve_dist_to_modules("python-dateutil") == ["dateutil"]
    assert resolve_dist_to_modules("pillow") == ["PIL"]
    assert "pkg_resources" in resolve_dist_to_modules("setuptools")
    assert resolve_dist_to_modules("requests") == ["requests"]
    assert resolve_dist_to_modules("unknown-lib") == ["unknown_lib"]
