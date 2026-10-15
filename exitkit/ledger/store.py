"""Cryptographic Evidence Store for content-addressed evidence artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from exitkit.ledger.integrity import Problem, VerifyResult, verify_evidence_file
from exitkit.ledger.models import Evidence, EvidenceKind
from exitkit.ledger.transitions import LedgerError

DEFAULT_EXTENSIONS: dict[EvidenceKind, str] = {
    EvidenceKind.HTTP_RESPONSE: "json",
    EvidenceKind.COMMAND_OUTPUT: "txt",
    EvidenceKind.TEST_RUN: "txt",
    EvidenceKind.FILE_SNAPSHOT: "bin",
    EvidenceKind.TOOL_CALL_LOG: "json",
    EvidenceKind.MODEL_RESPONSE: "json",
    EvidenceKind.DIFF: "patch",
    EvidenceKind.OTHER: "bin",
}


class EvidenceStore:
    """Manages content-addressed, sha256-verified evidence files on disk."""

    def __init__(self, run_dir: Path | str) -> None:
        self.run_dir = Path(run_dir).resolve()
        self.evidence_dir = self.run_dir / "evidence"
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = self.run_dir / "evidence_index.json"
        self._registry: dict[str, Evidence] = {}
        if self.index_file.exists():
            self._load_index()

    def _save_index(self) -> None:
        """Persist evidence index to disk."""
        data = {ev_id: ev.model_dump(mode="json") for ev_id, ev in self._registry.items()}
        self.index_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _load_index(self) -> None:
        """Load registered evidence items from disk."""
        try:
            raw = json.loads(self.index_file.read_text(encoding="utf-8"))
            for ev_id, ev_dict in raw.items():
                self._registry[ev_id] = Evidence.model_validate(ev_dict)
        except Exception:
            pass

    @classmethod
    def create_run_dir(cls, base_dir: Path | str = Path(".exitkit/runs")) -> Path:
        """Create a new timestamped run directory."""
        now_str = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        suffix = secrets.token_hex(3)
        run_id = f"{now_str}_{suffix}"
        run_path = Path(base_dir) / run_id
        run_path.mkdir(parents=True, exist_ok=True)
        (run_path / "evidence").mkdir(parents=True, exist_ok=True)
        return run_path

    def _sanitize_extension(self, ext: str) -> str:
        clean = ext.lstrip(".").strip().lower()
        if not clean or "/" in clean or "\\" in clean or ".." in clean or not clean.isalnum():
            return "bin"
        return clean

    def add_bytes(
        self,
        kind: EvidenceKind,
        content: bytes,
        description: str,
        command: str | None = None,
        exit_code: int | None = None,
        duration_seconds: float | None = None,
        ext: str | None = None,
        evidence_id: str | None = None,
    ) -> Evidence:
        """Atomically persist raw bytes into the content-addressed evidence store."""
        sha256_hash = hashlib.sha256(content).hexdigest()
        size_bytes = len(content)

        extension = self._sanitize_extension(ext) if ext else DEFAULT_EXTENSIONS.get(kind, "bin")
        prefix = kind.value.lower()
        short_hash = sha256_hash[:12]
        filename = f"{prefix}-{short_hash}.{extension}"

        # Prevent path traversal
        if "/" in filename or "\\" in filename or ".." in filename:
            raise LedgerError(f"Invalid evidence filename: {filename}")

        target_file = self.evidence_dir / filename
        rel_path = filename

        # Write atomically if file doesn't exist or is not matching
        if not target_file.exists():
            tmp_file = self.evidence_dir / f".tmp_{uuid4().hex}"
            try:
                tmp_file.write_bytes(content)
                # Attempt to set read-only (best-effort)
                try:
                    os.chmod(tmp_file, 0o444)
                except OSError:
                    pass
                tmp_file.replace(target_file)
            finally:
                if tmp_file.exists():
                    try:
                        tmp_file.unlink()
                    except OSError:
                        pass

        ev_id = evidence_id or f"ev-{short_hash}"
        evidence = Evidence(
            id=ev_id,
            kind=kind,
            path=rel_path,
            sha256=sha256_hash,
            size_bytes=size_bytes,
            created_at=datetime.now(UTC),
            description=description,
            command=command,
            exit_code=exit_code,
            duration_seconds=duration_seconds,
        )
        self._registry[ev_id] = evidence
        self._save_index()
        return evidence

    def add_text(
        self,
        kind: EvidenceKind,
        text: str,
        description: str,
        command: str | None = None,
        exit_code: int | None = None,
        duration_seconds: float | None = None,
        ext: str = "txt",
        evidence_id: str | None = None,
    ) -> Evidence:
        """Persist UTF-8 encoded text as evidence."""
        return self.add_bytes(
            kind=kind,
            content=text.encode("utf-8"),
            description=description,
            command=command,
            exit_code=exit_code,
            duration_seconds=duration_seconds,
            ext=ext,
            evidence_id=evidence_id,
        )

    def add_json(
        self,
        kind: EvidenceKind,
        data: Any,
        description: str,
        command: str | None = None,
        exit_code: int | None = None,
        duration_seconds: float | None = None,
        evidence_id: str | None = None,
    ) -> Evidence:
        """Persist JSON serializable structure as canonical UTF-8 JSON evidence."""
        content = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return self.add_bytes(
            kind=kind,
            content=content,
            description=description,
            command=command,
            exit_code=exit_code,
            duration_seconds=duration_seconds,
            ext="json",
            evidence_id=evidence_id,
        )

    def add_file_snapshot(
        self,
        kind: EvidenceKind,
        file_path: Path | str,
        description: str,
        command: str | None = None,
        exit_code: int | None = None,
        duration_seconds: float | None = None,
        evidence_id: str | None = None,
    ) -> Evidence:
        """Persist an existing file snapshot into the store."""
        src = Path(file_path).resolve()
        if not src.exists():
            raise FileNotFoundError(f"Source file snapshot does not exist: {src}")
        content = src.read_bytes()
        ext = src.suffix.lstrip(".") or "bin"
        return self.add_bytes(
            kind=kind,
            content=content,
            description=description,
            command=command,
            exit_code=exit_code,
            duration_seconds=duration_seconds,
            ext=ext,
            evidence_id=evidence_id,
        )

    def get(self, evidence_id: str) -> Evidence | None:
        """Retrieve evidence metadata by ID if stored in this session."""
        return self._registry.get(evidence_id)

    def get_content_bytes(self, evidence: Evidence) -> bytes:
        """Read the raw content bytes of an evidence file."""
        file_path = self.evidence_dir / evidence.path
        if not file_path.exists():
            raise FileNotFoundError(f"Evidence file missing on disk: {file_path}")
        return file_path.read_bytes()

    def verify(self, evidence: Evidence) -> VerifyResult:
        """Verify the cryptographic hash and size of an evidence file on disk."""
        problems = verify_evidence_file(evidence, self.evidence_dir)
        return VerifyResult(valid=len(problems) == 0, problems=problems)

    def verify_all(self) -> list[Problem]:
        """Verify all registered evidence items in this store."""
        all_problems: list[Problem] = []
        for ev in self._registry.values():
            all_problems.extend(verify_evidence_file(ev, self.evidence_dir))
        return all_problems
