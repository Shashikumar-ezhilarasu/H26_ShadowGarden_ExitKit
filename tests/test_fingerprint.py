"""Tests for filesystem tree fingerprinting."""

from __future__ import annotations

from pathlib import Path

from exitkit.util.fingerprint import tree_fingerprint


def test_tree_fingerprint_stable_and_deterministic(tmp_path: Path) -> None:
    """Fingerprint is identical across multiple calls on an unchanged directory."""
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("hello world\n", encoding="utf-8")

    fp1 = tree_fingerprint(tmp_path)
    fp2 = tree_fingerprint(tmp_path)

    assert fp1 == fp2
    assert len(fp1) == 64


def test_tree_fingerprint_detects_one_byte_change(tmp_path: Path) -> None:
    """Changing a single byte in any file modifies the resulting fingerprint."""
    file1 = tmp_path / "code.py"
    file1.write_text("def run(): return 42\n", encoding="utf-8")

    fp_before = tree_fingerprint(tmp_path)

    # Modify one byte
    file1.write_text("def run(): return 43\n", encoding="utf-8")
    fp_after = tree_fingerprint(tmp_path)

    assert fp_before != fp_after


def test_tree_fingerprint_ignores_git_and_pycache(tmp_path: Path) -> None:
    """Fingerprint ignores internal VCS, Python, and testing caches."""
    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")
    fp_base = tree_fingerprint(tmp_path)

    # Add .git directory with contents
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("[core]\n", encoding="utf-8")

    # Add __pycache__ directory with bytecode
    pycache_dir = tmp_path / "__pycache__"
    pycache_dir.mkdir()
    (pycache_dir / "main.cpython-312.pyc").write_bytes(b"\x00\x01\x02")

    # Add .venv and .exitkit
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    (tmp_path / ".exitkit").mkdir()
    (tmp_path / ".exitkit" / "state.json").write_text("{}", encoding="utf-8")

    fp_with_ignored = tree_fingerprint(tmp_path)

    # Fingerprint must remain completely identical
    assert fp_base == fp_with_ignored
