"""Tests for offline capsule verification, tamper detection, and trust ledger audits."""

from __future__ import annotations

import json
from pathlib import Path

from exitkit.capsule.models import CapsuleCheckStatus
from exitkit.capsule.verifier import verify_capsule
from exitkit.capsule.writer import build_capsule
from tests.capsule.test_writer import _setup_mock_run_dir


def test_verify_valid_capsule_passes(tmp_path: Path) -> None:
    """An untampered capsule bundle passes all integrity and trust audits."""
    run_dir = _setup_mock_run_dir(tmp_path)
    capsule_dir = tmp_path / "valid_capsule"
    build_capsule(run_dir=run_dir, output_dir=capsule_dir, archive=False)

    res = verify_capsule(capsule_dir)

    assert res.is_valid is True
    assert res.status == CapsuleCheckStatus.VERIFIED
    assert len(res.tampered_files) == 0
    assert any(c.name == "trust_ledger_integrity" and c.passed for c in res.checks)


def test_tamper_detection_flip_byte_in_evidence_fails(tmp_path: Path) -> None:
    """Flipping one byte in an evidence file causes verification failure with sha256 mismatch."""
    run_dir = _setup_mock_run_dir(tmp_path)
    capsule_dir = tmp_path / "tampered_capsule"
    build_capsule(run_dir=run_dir, output_dir=capsule_dir, archive=False)

    # Locate an evidence file and flip one byte
    ev_files = list((capsule_dir / "evidence").iterdir())
    assert len(ev_files) > 0
    target_ev = ev_files[0]

    original_bytes = target_ev.read_bytes()
    tampered_bytes = bytearray(original_bytes)
    tampered_bytes[0] ^= 0x01
    target_ev.chmod(0o644)
    target_ev.write_bytes(tampered_bytes)

    res = verify_capsule(capsule_dir)

    assert res.is_valid is False
    assert res.status == CapsuleCheckStatus.TAMPERED
    rel_name = f"evidence/{target_ev.name}"
    assert rel_name in res.tampered_files

    # Check details contain expected vs actual
    failed_check = next(c for c in res.checks if not c.passed and c.expected)
    assert failed_check.expected != failed_check.actual


def test_verify_tar_archive(tmp_path: Path) -> None:
    """Verification directly unpacks and audits a .tar.gz archive bundle."""
    run_dir = _setup_mock_run_dir(tmp_path)
    tar_out = tmp_path / "archive_capsule.tar.gz"
    build_capsule(run_dir=run_dir, output_dir=tar_out, archive=True)

    res = verify_capsule(tar_out)
    assert res.is_valid is True
    assert res.status == CapsuleCheckStatus.VERIFIED


def test_trust_ledger_violation_caught(tmp_path: Path) -> None:
    """Violating the trust state machine (e.g. AI claim marked OBSERVED) is rejected."""
    run_dir = _setup_mock_run_dir(tmp_path)
    capsule_dir = tmp_path / "trust_violating_capsule"
    build_capsule(run_dir=run_dir, output_dir=capsule_dir, archive=False)

    capsule_file = capsule_dir / "capsule.json"
    data = json.loads(capsule_file.read_text(encoding="utf-8"))

    # Illegal state: claim has trust_level OBSERVED but source MODEL
    data["claims"][0]["trust_level"] = "OBSERVED"
    data["claims"][0]["source"] = "MODEL"
    capsule_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # Update manifest to isolate the test to trust violation rather than checksum
    manifest_lines = []
    for f in sorted(capsule_dir.rglob("*")):
        if f.is_file() and f.name != "SHA256SUMS":
            import hashlib

            h = hashlib.sha256(f.read_bytes()).hexdigest()
            manifest_lines.append(f"{h}  {f.relative_to(capsule_dir).as_posix()}\n")
    (capsule_dir / "SHA256SUMS").write_text("".join(manifest_lines), encoding="utf-8")

    res = verify_capsule(capsule_dir)
    assert res.is_valid is False
    assert res.status in (CapsuleCheckStatus.SCHEMA_INVALID, CapsuleCheckStatus.TRUST_VIOLATION)
