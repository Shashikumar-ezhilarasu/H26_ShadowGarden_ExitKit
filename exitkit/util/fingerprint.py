"""Filesystem tree fingerprinting for tamper-detection and integrity verification."""

from __future__ import annotations

import hashlib
from pathlib import Path

IGNORED_DIR_NAMES = {
    ".git",
    "__pycache__",
    ".venv",
    ".exitkit",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
}

IGNORED_FILE_NAMES = {
    ".DS_Store",
}


def tree_fingerprint(path: Path | str) -> str:
    """Compute a deterministic SHA-256 fingerprint over all regular files in a directory tree.

    Sorted relative paths and their raw byte contents are hashed together.
    Internal build/VCS caches (.git, __pycache__, .venv, .exitkit) are excluded.
    """
    root = Path(path).resolve()
    if not root.exists():
        raise FileNotFoundError(f"Path does not exist: {root}")
    if root.is_file():
        hasher = hashlib.sha256()
        hasher.update(root.read_bytes())
        return hasher.hexdigest()

    entries: list[tuple[str, Path]] = []
    for item in root.rglob("*"):
        # Skip if any parent component is in ignored dirs
        rel_parts = item.relative_to(root).parts
        if any(part in IGNORED_DIR_NAMES for part in rel_parts):
            continue
        if item.name in IGNORED_FILE_NAMES:
            continue
        if item.is_file() and not item.is_symlink():
            rel_str = "/".join(rel_parts)
            entries.append((rel_str, item))

    # Deterministic sort by POSIX relative path
    entries.sort(key=lambda pair: pair[0])

    hasher = hashlib.sha256()
    for rel_path, file_path in entries:
        hasher.update(rel_path.encode("utf-8"))
        hasher.update(b"\x00")
        try:
            hasher.update(file_path.read_bytes())
        except OSError as exc:
            raise OSError(f"Failed to read file during fingerprinting: {file_path}") from exc
        hasher.update(b"\x00")

    return hasher.hexdigest()
