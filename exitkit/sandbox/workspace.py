"""Isolated workspace management with cryptographic immutability guarantees (Rule 5)."""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import tempfile
from collections.abc import Generator
from pathlib import Path

from exitkit.util.fingerprint import tree_fingerprint


def _copy_ignore(path: str, names: list[str]) -> set[str]:
    """Ignore caches and virtual environments during workspace copy."""
    ignored: set[str] = set()
    for name in names:
        if name in (".git", "__pycache__", ".pytest_cache", ".venv", ".exitkit", ".targets"):
            ignored.add(name)
        elif name.endswith(".pyc") or name.endswith(".pyo"):
            ignored.add(name)
    return ignored


@contextlib.contextmanager
def isolated_workspace(
    target_repo: Path | str,
    base_dir: Path | str | None = None,
) -> Generator[Path, None, None]:
    """Create an isolated workspace clone or worktree and enforce original immutability.

    Yields:
        Path to the isolated workspace directory.

    Raises:
        RuntimeError: If target repository tree fingerprint changes during execution (Rule 5).
    """
    orig_path = Path(target_repo).resolve()
    if not orig_path.exists():
        raise FileNotFoundError(f"Target repository not found: {orig_path}")

    # Sacred Invariant (Rule 5): Record fingerprint before any operations
    fp_before = tree_fingerprint(orig_path)

    # Create temporary container
    container_base = Path(base_dir).resolve() if base_dir else None
    if container_base:
        container_base.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix="exitkit_ws_", dir=container_base))
    workspace_path = temp_dir / orig_path.name

    is_worktree = False
    git_dir = orig_path / ".git"

    # Attempt git worktree if valid git repository
    if git_dir.exists():
        try:
            cmd = [
                "git",
                "-C",
                str(orig_path),
                "worktree",
                "add",
                "--detach",
                str(workspace_path),
                "HEAD",
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if proc.returncode == 0 and workspace_path.exists():
                is_worktree = True
        except Exception:
            is_worktree = False

    # Fallback to isolated filesystem copy
    if not is_worktree:
        if workspace_path.exists():
            shutil.rmtree(workspace_path, ignore_errors=True)
        shutil.copytree(orig_path, workspace_path, ignore=_copy_ignore, symlinks=True)

    try:
        yield workspace_path
    finally:
        # Cleanup isolated workspace
        if is_worktree:
            try:
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(orig_path),
                        "worktree",
                        "remove",
                        "--force",
                        str(workspace_path),
                    ],
                    capture_output=True,
                    check=False,
                )
            except Exception:
                pass

        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)

        # Sacred Invariant (Rule 5): Assert target repository was untouched
        fp_after = tree_fingerprint(orig_path)
        if fp_before != fp_after:
            raise RuntimeError(
                f"Target repository safety violation: tree fingerprint changed in {orig_path}!\n"
                f"Fingerprint before: {fp_before}\n"
                f"Fingerprint after:  {fp_after}"
            )
