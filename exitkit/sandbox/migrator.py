"""Automated application of migration proposals producing clean unified diffs."""

from __future__ import annotations

import difflib
import re
from pathlib import Path

from exitkit.ledger.models import ProposalKind
from exitkit.llm.models import MigrationProposal


def _update_manifest_text(
    content: str,
    target_dep: str,
    replacement: str,
    kind: ProposalKind,
) -> str:
    """Update dependency references in requirements or pyproject files."""
    lines = content.splitlines(keepends=True)
    new_lines: list[str] = []

    for line in lines:
        # Match dependency line e.g. "appdirs>=1.4.0", "appdirs==1.4.4", '"appdirs>=1.4.0"'
        pattern = rf"(^|[\"'\s])({re.escape(target_dep)})([=><~!\s\"',]|$)"
        if re.search(pattern, line, flags=re.IGNORECASE):
            if kind == ProposalKind.REPLACE:
                # Replace with replacement specifier
                subbed = re.sub(
                    rf"([\"']?){re.escape(target_dep)}([=><~!0-9\.\*]*)([\"']?)",
                    rf"\1{replacement}\3",
                    line,
                    flags=re.IGNORECASE,
                )
                new_lines.append(subbed)
            elif kind == ProposalKind.UPGRADE:
                subbed = re.sub(
                    rf"([\"']?){re.escape(target_dep)}([=><~!0-9\.\*]*)([\"']?)",
                    rf"\1{replacement}\3",
                    line,
                    flags=re.IGNORECASE,
                )
                new_lines.append(subbed)
            else:
                # REMOVE_OR_VENDOR: comment out or remove line
                new_lines.append(f"# Removed by ExitKit: {line.strip()}\n")
        else:
            new_lines.append(line)

    return "".join(new_lines)


def _rewrite_source_imports(
    code: str,
    target_dep: str,
    replacement_pkg: str,
) -> str:
    """Rewrite Python import statements from target dependency to replacement."""
    # Pattern 1: from target_dep import ... -> from replacement_pkg import ...
    c1 = re.sub(
        rf"\bfrom\s+{re.escape(target_dep)}(\.[a-zA-Z0-9_]+)?\s+import\b",
        rf"from {replacement_pkg}\1 import",
        code,
    )
    # Pattern 2: import target_dep as ... -> import replacement_pkg as ...
    c2 = re.sub(
        rf"\bimport\s+{re.escape(target_dep)}\b",
        rf"import {replacement_pkg}",
        c1,
    )
    return c2


def apply_migration(
    workspace: Path,
    proposal: MigrationProposal,
) -> tuple[str, list[Path]]:
    """Apply the migration proposal to the isolated workspace and generate a unified diff.

    Returns:
        tuple of (unified_diff_string, list_of_modified_file_paths)
    """
    modified_files: list[Path] = []
    diff_chunks: list[str] = []

    target_dep = proposal.target_dependency
    replacement_pkg = proposal.proposed_package
    replacement_spec = f"{replacement_pkg}{proposal.proposed_specifier}"

    # 1. Update manifest files
    manifest_candidates = [
        workspace / "pyproject.toml",
        workspace / "requirements.txt",
        workspace / "requirements-dev.txt",
        workspace / "setup.cfg",
    ]

    for m_file in manifest_candidates:
        if not m_file.exists():
            continue
        original = m_file.read_text(encoding="utf-8")
        updated = _update_manifest_text(
            content=original,
            target_dep=target_dep,
            replacement=replacement_spec,
            kind=proposal.kind,
        )
        if original != updated:
            m_file.write_text(updated, encoding="utf-8")
            modified_files.append(m_file)
            diff = difflib.unified_diff(
                original.splitlines(keepends=True),
                updated.splitlines(keepends=True),
                fromfile=f"a/{m_file.relative_to(workspace)}",
                tofile=f"b/{m_file.relative_to(workspace)}",
            )
            diff_chunks.extend(diff)

    # 2. Source code import rewrites (for REPLACE migrations)
    if proposal.kind == ProposalKind.REPLACE:
        for py_file in workspace.glob("**/*.py"):
            if ".venv" in py_file.parts or ".git" in py_file.parts:
                continue
            original_code = py_file.read_text(encoding="utf-8", errors="replace")
            updated_code = _rewrite_source_imports(
                code=original_code,
                target_dep=target_dep,
                replacement_pkg=replacement_pkg,
            )
            if original_code != updated_code:
                py_file.write_text(updated_code, encoding="utf-8")
                modified_files.append(py_file)
                diff = difflib.unified_diff(
                    original_code.splitlines(keepends=True),
                    updated_code.splitlines(keepends=True),
                    fromfile=f"a/{py_file.relative_to(workspace)}",
                    tofile=f"b/{py_file.relative_to(workspace)}",
                )
                diff_chunks.extend(diff)

    # 3. Write shim module if specified
    if proposal.expected_shim_needed and proposal.shim_code:
        shim_path = workspace / f"_exitkit_{replacement_pkg}_shim.py"
        shim_path.write_text(proposal.shim_code, encoding="utf-8")
        modified_files.append(shim_path)
        diff = difflib.unified_diff(
            [],
            proposal.shim_code.splitlines(keepends=True),
            fromfile="/dev/null",
            tofile=f"b/{shim_path.relative_to(workspace)}",
        )
        diff_chunks.extend(diff)

    unified_patch = "".join(diff_chunks)
    return unified_patch, modified_files
