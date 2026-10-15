"""Resolves Python import module names to distribution package names."""

from __future__ import annotations

import importlib.metadata
import sys

from packaging.utils import canonicalize_name

# Standard library module names (available in Python 3.10+)
STDLIB_MODULES = frozenset(sys.stdlib_module_names)

# Known static mappings between import module name and canonical distribution name
KNOWN_MODULE_TO_DIST: dict[str, str] = {
    "yaml": "pyyaml",
    "dateutil": "python-dateutil",
    "PIL": "pillow",
    "bs4": "beautifulsoup4",
    "dotenv": "python-dotenv",
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "pkg_resources": "setuptools",
    "setuptools": "setuptools",
    "appdirs": "appdirs",
    "platformdirs": "platformdirs",
    "toml": "toml",
    "tomllib": "tomllib",
    "jinja2": "jinja2",
    "markupsafe": "markupsafe",
    "requests": "requests",
    "urllib3": "urllib3",
    "httpx": "httpx",
    "click": "click",
    "rich": "rich",
    "typer": "typer",
    "pydantic": "pydantic",
    "pydantic_core": "pydantic-core",
    "pytest": "pytest",
    "nose": "nose",
    "pygments": "pygments",
    "chardet": "chardet",
    "idna": "idna",
    "six": "six",
}

# Known distribution name to imported top-level modules
KNOWN_DIST_TO_MODULES: dict[str, list[str]] = {
    "pyyaml": ["yaml"],
    "python-dateutil": ["dateutil"],
    "pillow": ["PIL"],
    "beautifulsoup4": ["bs4"],
    "python-dotenv": ["dotenv"],
    "opencv-python": ["cv2"],
    "scikit-learn": ["sklearn"],
    "setuptools": ["setuptools", "pkg_resources"],
    "appdirs": ["appdirs"],
    "platformdirs": ["platformdirs"],
    "toml": ["toml"],
    "jinja2": ["jinja2"],
    "requests": ["requests"],
    "urllib3": ["urllib3"],
    "httpx": ["httpx"],
    "click": ["click"],
    "rich": ["rich"],
    "typer": ["typer"],
    "pydantic": ["pydantic"],
    "pytest": ["pytest"],
    "nose": ["nose"],
    "pygments": ["pygments"],
    "chardet": ["chardet"],
    "idna": ["idna"],
    "six": ["six"],
}


def is_stdlib_module(name: str) -> bool:
    """Return True if the top-level module is part of the Python standard library."""
    top_level = name.split(".")[0]
    return top_level in STDLIB_MODULES


def resolve_module_to_dist(
    module_name: str,
    declared_dists: set[str] | None = None,
) -> str | None:
    """Map a top-level import name to its canonical distribution name."""
    top_level = module_name.split(".")[0]

    # Ignore standard library modules
    if is_stdlib_module(top_level):
        return None

    # 1. Check runtime environment's packages_distributions()
    try:
        pkgs_map = importlib.metadata.packages_distributions()
        dists = pkgs_map.get(top_level)
        if dists:
            return canonicalize_name(dists[0])
    except Exception:
        pass

    # 2. Check static known mapping
    if top_level in KNOWN_MODULE_TO_DIST:
        return canonicalize_name(KNOWN_MODULE_TO_DIST[top_level])

    # 3. Check declared distributions
    if declared_dists:
        canon_top = canonicalize_name(top_level)
        if canon_top in declared_dists:
            return canon_top
        # Check if any declared dist maps to this module
        for d in declared_dists:
            mods = KNOWN_DIST_TO_MODULES.get(d, [d])
            if top_level in mods:
                return d

    # 4. Fallback to canonicalized module name
    return canonicalize_name(top_level)


def resolve_dist_to_modules(dist_name: str) -> list[str]:
    """Return all known top-level module names for a given distribution."""
    canon = canonicalize_name(dist_name)
    if canon in KNOWN_DIST_TO_MODULES:
        return list(KNOWN_DIST_TO_MODULES[canon])

    # Try inverse lookup in packages_distributions
    try:
        pkgs_map = importlib.metadata.packages_distributions()
        modules: list[str] = []
        for mod, dists in pkgs_map.items():
            if any(canonicalize_name(d) == canon for d in dists):
                modules.append(mod)
        if modules:
            return sorted(modules)
    except Exception:
        pass

    # Default to the canonical distribution name as the module name
    return [canon.replace("-", "_")]
