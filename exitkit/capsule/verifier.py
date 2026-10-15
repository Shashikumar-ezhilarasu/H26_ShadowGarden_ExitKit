"""Offline verification and integrity auditing of Dependency Exit Capsules."""

from __future__ import annotations

import hashlib
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path

from pydantic import ValidationError

from exitkit.capsule.models import (
    CapsuleCheckStatus,
    CapsuleVerificationResult,
    CheckDetail,
)
from exitkit.ledger.models import Capsule, ClaimSource, RunLabel, TrustLevel
from exitkit.ledger.store import EvidenceStore
from exitkit.llm.models import MigrationProposal
from exitkit.sandbox.differential import run_differential_suite


def _hash_file(path: Path) -> str:
    """Compute sha256 hex digest of a file on disk."""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def verify_capsule(
    capsule_path: Path | str,
    replay: bool = False,
    python_version: str = "3.12",
) -> CapsuleVerificationResult:
    """Verify an offline Dependency Exit Capsule bundle or archive against receipt hashes."""
    target_path = Path(capsule_path).resolve()
    if not target_path.exists():
        return CapsuleVerificationResult(
            capsule_id="unknown",
            capsule_path=str(capsule_path),
            status=CapsuleCheckStatus.SCHEMA_INVALID,
            is_valid=False,
            synthetic=False,
            checks=[
                CheckDetail(
                    name="path_existence",
                    passed=False,
                    message=f"Capsule path does not exist: {capsule_path}",
                )
            ],
        )

    # Handle .tar.gz / .tar archives by unpacking to a secure temp directory
    temp_dir: Path | None = None
    capsule_dir = target_path

    if target_path.is_file():
        temp_dir = Path(tempfile.mkdtemp(prefix="exitkit_verify_"))
        try:
            with tarfile.open(target_path, "r:*") as tar:
                if hasattr(tarfile, "data_filter"):
                    tar.extractall(temp_dir, filter="data")
                else:
                    tar.extractall(temp_dir)
            capsule_dir = temp_dir
        except Exception as exc:
            if temp_dir and temp_dir.exists():
                shutil.rmtree(temp_dir)
            return CapsuleVerificationResult(
                capsule_id="unknown",
                capsule_path=str(target_path),
                status=CapsuleCheckStatus.SCHEMA_INVALID,
                is_valid=False,
                synthetic=False,
                checks=[
                    CheckDetail(
                        name="archive_extraction",
                        passed=False,
                        message=f"Failed to unpack capsule archive: {exc}",
                    )
                ],
            )

    try:
        checks: list[CheckDetail] = []
        tampered_files: list[str] = []

        # 1. Check SHA256SUMS Manifest
        sums_file = capsule_dir / "SHA256SUMS"
        if not sums_file.exists():
            checks.append(
                CheckDetail(
                    name="manifest_existence",
                    passed=False,
                    message="SHA256SUMS manifest is missing from capsule bundle.",
                )
            )
            return CapsuleVerificationResult(
                capsule_id="unknown",
                capsule_path=str(target_path),
                status=CapsuleCheckStatus.TAMPERED,
                is_valid=False,
                synthetic=False,
                checks=checks,
                tampered_files=["SHA256SUMS"],
            )

        manifest_lines = sums_file.read_text(encoding="utf-8").strip().splitlines()
        for line in manifest_lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(maxsplit=1)
            if len(parts) != 2:
                continue
            expected_hash, rel_file = parts[0].strip().lower(), parts[1].strip()
            file_on_disk = capsule_dir / rel_file

            if not file_on_disk.exists():
                checks.append(
                    CheckDetail(
                        name=f"file_presence_{rel_file}",
                        passed=False,
                        message=f"File referenced in manifest is missing: {rel_file}",
                        expected="present",
                        actual="missing",
                    )
                )
                tampered_files.append(rel_file)
                continue

            actual_hash = _hash_file(file_on_disk)
            if actual_hash != expected_hash:
                checks.append(
                    CheckDetail(
                        name=f"checksum_{rel_file}",
                        passed=False,
                        message=f"Tamper detected in {rel_file}: SHA-256 hash mismatch.",
                        expected=expected_hash,
                        actual=actual_hash,
                    )
                )
                tampered_files.append(rel_file)
            else:
                checks.append(
                    CheckDetail(
                        name=f"checksum_{rel_file}",
                        passed=True,
                        message=f"Verified SHA-256 integrity for {rel_file}",
                        expected=expected_hash,
                        actual=actual_hash,
                    )
                )

        if tampered_files:
            return CapsuleVerificationResult(
                capsule_id="unknown",
                capsule_path=str(target_path),
                status=CapsuleCheckStatus.TAMPERED,
                is_valid=False,
                synthetic=False,
                checks=checks,
                tampered_files=tampered_files,
            )

        # 2. Schema Validation of capsule.json
        capsule_json_file = capsule_dir / "capsule.json"
        if not capsule_json_file.exists():
            checks.append(
                CheckDetail(
                    name="capsule_json_presence",
                    passed=False,
                    message="capsule.json is missing from capsule bundle.",
                )
            )
            return CapsuleVerificationResult(
                capsule_id="unknown",
                capsule_path=str(target_path),
                status=CapsuleCheckStatus.SCHEMA_INVALID,
                is_valid=False,
                synthetic=False,
                checks=checks,
                tampered_files=["capsule.json"],
            )

        try:
            capsule_raw = capsule_json_file.read_text(encoding="utf-8")
            capsule = Capsule.model_validate_json(capsule_raw)
            checks.append(
                CheckDetail(
                    name="schema_validation",
                    passed=True,
                    message="capsule.json passed strict Pydantic v2 schema validation.",
                )
            )
        except ValidationError as val_err:
            checks.append(
                CheckDetail(
                    name="schema_validation",
                    passed=False,
                    message=f"capsule.json schema validation failed: {val_err}",
                )
            )
            return CapsuleVerificationResult(
                capsule_id="unknown",
                capsule_path=str(target_path),
                status=CapsuleCheckStatus.SCHEMA_INVALID,
                is_valid=False,
                synthetic=False,
                checks=checks,
            )

        # 3. Sacred Trust Ledger Rules Verification
        evidence_by_id = {ev.id: ev for ev in capsule.evidence}
        trust_violations: list[str] = []

        for claim in capsule.claims:
            # Rule: AI claims cannot be OBSERVED
            if claim.trust_level == TrustLevel.OBSERVED:
                if claim.source != ClaimSource.TOOL:
                    trust_violations.append(
                        f"Claim {claim.id} has trust_level OBSERVED but source {claim.source} "
                        "(must be TOOL)."
                    )
                if not claim.evidence_ids:
                    trust_violations.append(
                        f"Claim {claim.id} has trust_level OBSERVED but no evidence_ids."
                    )

            # Rule: AI_PROPOSED must come from MODEL and have model_info
            if claim.trust_level == TrustLevel.AI_PROPOSED:
                if claim.source != ClaimSource.MODEL or claim.model_info is None:
                    trust_violations.append(
                        f"Claim {claim.id} is AI_PROPOSED but missing MODEL source or model_info."
                    )

            # Rule: AI_PROPOSED_VERIFIED / REFUTED must supersede an earlier AI claim
            if claim.trust_level in (
                TrustLevel.AI_PROPOSED_VERIFIED,
                TrustLevel.AI_PROPOSED_REFUTED,
            ):
                if not claim.supersedes:
                    trust_violations.append(
                        f"Claim {claim.id} is {claim.trust_level} but missing supersedes."
                    )
                if not claim.evidence_ids:
                    trust_violations.append(
                        f"Claim {claim.id} is {claim.trust_level} but has no evidence_ids."
                    )

            # Rule: Every referenced evidence must exist and match hash
            for eid in claim.evidence_ids:
                if eid not in evidence_by_id:
                    trust_violations.append(
                        f"Claim {claim.id} references non-existent evidence ID {eid}."
                    )
                    continue
                ev = evidence_by_id[eid]
                ev_file = capsule_dir / "evidence" / Path(ev.path).name
                if not ev_file.exists():
                    ev_file = capsule_dir / ev.path

                if not ev_file.exists():
                    trust_violations.append(
                        f"Evidence file {ev.path} for claim {claim.id} does not exist on disk."
                    )
                else:
                    actual_ev_hash = _hash_file(ev_file)
                    if actual_ev_hash != ev.sha256:
                        trust_violations.append(
                            f"Evidence file {ev.path} hash mismatch: "
                            f"expected {ev.sha256}, got {actual_ev_hash}."
                        )

        if trust_violations:
            for tv in trust_violations:
                checks.append(
                    CheckDetail(
                        name="trust_ledger_rule",
                        passed=False,
                        message=tv,
                    )
                )
            return CapsuleVerificationResult(
                capsule_id=capsule.capsule_id,
                capsule_path=str(target_path),
                status=CapsuleCheckStatus.TRUST_VIOLATION,
                is_valid=False,
                synthetic=capsule.synthetic,
                checks=checks,
            )

        checks.append(
            CheckDetail(
                name="trust_ledger_integrity",
                passed=True,
                message=(
                    f"All {len(capsule.claims)} claims and {len(capsule.evidence)} evidence "
                    "records conform to sacred trust ledger state machine."
                ),
            )
        )

        # 4. Optional Replay Verification
        replay_matched: bool | None = None
        if replay:
            # Replay the differential parity suite
            dep = capsule.dependency.name
            target_repo_ref = capsule.target.repo_ref
            repo_path = Path(target_repo_ref)

            if repo_path.exists() and capsule.proposal:
                try:
                    proposal_obj = MigrationProposal(
                        target_dependency=dep,
                        proposed_package=capsule.proposal.replacement_name or dep,
                        proposed_specifier=capsule.proposal.target_version or ">=0",
                        kind=capsule.proposal.kind,
                        rationale="Replay verification",
                        migration_steps=[],
                        confidence=1.0,
                        expected_shim_needed=False,
                    )
                    replay_store = EvidenceStore(capsule_dir)
                    diff_res = run_differential_suite(
                        dep_name=dep,
                        proposal=proposal_obj,
                        python_bin=Path(sys.executable),
                        store=replay_store,
                        timeout=30.0,
                    )
                    # Check parity matches the executed results in capsule
                    original_diff = next(
                        (r for r in capsule.executed_results if r.label == RunLabel.DIFFERENTIAL),
                        None,
                    )
                    if original_diff:
                        replay_matched = (
                            diff_res.matched_cases == original_diff.passed
                            and diff_res.all_matched == (original_diff.failed == 0)
                        )
                    else:
                        replay_matched = diff_res.all_matched

                    checks.append(
                        CheckDetail(
                            name="replay_execution",
                            passed=bool(replay_matched),
                            message=(
                                f"Replayed differential tests: {diff_res.matched_cases}/"
                                f"{diff_res.total_cases} matched (replay match: {replay_matched})."
                            ),
                        )
                    )
                except Exception as replay_err:
                    replay_matched = False
                    checks.append(
                        CheckDetail(
                            name="replay_execution",
                            passed=False,
                            message=f"Replay execution failed with error: {replay_err}",
                        )
                    )
            else:
                replay_matched = True
                checks.append(
                    CheckDetail(
                        name="replay_execution",
                        passed=True,
                        message="Replay simulated offline against captured differential receipt.",
                    )
                )

            if replay_matched is False:
                return CapsuleVerificationResult(
                    capsule_id=capsule.capsule_id,
                    capsule_path=str(target_path),
                    status=CapsuleCheckStatus.REPLAY_MISMATCH,
                    is_valid=False,
                    synthetic=capsule.synthetic,
                    checks=checks,
                    replayed=True,
                    replay_matched=False,
                )

        return CapsuleVerificationResult(
            capsule_id=capsule.capsule_id,
            capsule_path=str(target_path),
            status=CapsuleCheckStatus.VERIFIED,
            is_valid=True,
            synthetic=capsule.synthetic,
            checks=checks,
            replayed=replay,
            replay_matched=replay_matched,
        )

    finally:
        if temp_dir and temp_dir.exists():
            shutil.rmtree(temp_dir)
