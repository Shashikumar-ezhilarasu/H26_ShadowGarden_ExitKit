"""Integrity validation and evidence verification for capsules."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from exitkit.ledger.models import Capsule, Evidence


@dataclass(frozen=True)
class Problem:
    """An integrity failure detected during evidence verification."""

    evidence_id: str
    problem_type: str  # "MISSING_FILE", "HASH_MISMATCH", "SIZE_MISMATCH", "PATH_TRAVERSAL"
    message: str


@dataclass(frozen=True)
class VerifyResult:
    """Outcome of verifying one or more evidence items."""

    valid: bool
    problems: list[Problem]

    @property
    def problem_summary(self) -> str:
        """Formatted summary of verification problems."""
        if self.valid:
            return "All evidence items verified successfully."
        return "; ".join(f"[{p.problem_type}] {p.evidence_id}: {p.message}" for p in self.problems)


def verify_evidence_file(evidence: Evidence, evidence_root: Path | str) -> list[Problem]:
    """Verify that a single Evidence record matches its physical file on disk."""
    root = Path(evidence_root).resolve()
    rel_path = evidence.path.replace("\\", "/")

    if rel_path.startswith("/") or ".." in rel_path.split("/"):
        return [
            Problem(
                evidence_id=evidence.id,
                problem_type="PATH_TRAVERSAL",
                message=(
                    f"Path traversal or absolute path detected in evidence path: {evidence.path}"
                ),
            )
        ]

    file_path = root / rel_path
    if not file_path.exists():
        return [
            Problem(
                evidence_id=evidence.id,
                problem_type="MISSING_FILE",
                message=f"Evidence file missing on disk: {file_path}",
            )
        ]

    try:
        content = file_path.read_bytes()
    except OSError as exc:
        return [
            Problem(
                evidence_id=evidence.id,
                problem_type="READ_ERROR",
                message=f"Failed to read evidence file: {exc}",
            )
        ]

    problems: list[Problem] = []
    if len(content) != evidence.size_bytes:
        problems.append(
            Problem(
                evidence_id=evidence.id,
                problem_type="SIZE_MISMATCH",
                message=(
                    f"File size mismatch: record specifies {evidence.size_bytes} bytes, "
                    f"file on disk is {len(content)} bytes"
                ),
            )
        )

    computed_hash = hashlib.sha256(content).hexdigest()
    if computed_hash != evidence.sha256:
        problems.append(
            Problem(
                evidence_id=evidence.id,
                problem_type="HASH_MISMATCH",
                message=(
                    f"SHA-256 hash mismatch: record specifies {evidence.sha256}, "
                    f"computed {computed_hash}"
                ),
            )
        )

    return problems


def verify_capsule_evidence(capsule: Capsule, evidence_root: Path | str) -> list[Problem]:
    """Verify all evidence items declared in a Capsule against files on disk."""
    problems: list[Problem] = []
    for ev in capsule.evidence:
        problems.extend(verify_evidence_file(ev, evidence_root))
    return problems
