"""Tests for the bounded proposer orchestrator and ledger claim generation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from exitkit.ledger.models import ClaimSource, EvidenceKind, ProposalKind, TrustLevel
from exitkit.ledger.store import EvidenceStore
from exitkit.llm.client import MockModelClient
from exitkit.llm.proposer import run_propose
from exitkit.util.fingerprint import tree_fingerprint


@pytest.fixture
def synthetic_target(tmp_path: Path) -> Path:
    """Create a minimal synthetic Python project fixture."""
    repo = tmp_path / "sample_app"
    repo.mkdir()

    # Create pyproject.toml
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(
        """[project]
name = "sample-app"
version = "0.1.0"
dependencies = [
    "appdirs>=1.4.4",
    "jinja2==2.11.3",
]
""",
        encoding="utf-8",
    )

    # Create source code using appdirs
    src = repo / "sample_app"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "cache.py").write_text(
        """from appdirs import user_cache_dir

def get_cache() -> str:
    return user_cache_dir("sample-app", "author")
""",
        encoding="utf-8",
    )

    return repo


def test_run_propose_creates_sacred_claim_and_evidence(
    synthetic_target: Path, tmp_path: Path
) -> None:
    """run_propose generates AI_PROPOSED claim, stores evidence, and leaves repo untouched."""
    run_dir = tmp_path / "test_run"
    fp_before = tree_fingerprint(synthetic_target)

    client = MockModelClient()
    result = run_propose(
        repo_path=synthetic_target,
        dep_name="appdirs",
        client=client,
        run_dir=run_dir,
    )

    # 1. Output models
    assert result.target_dependency == "appdirs"
    assert result.proposal.kind == ProposalKind.REPLACE
    assert result.proposal.proposed_package == "platformdirs"

    # 2. Sacred Claim verification
    claim = result.claim
    assert claim.trust_level == TrustLevel.AI_PROPOSED
    assert claim.source == ClaimSource.MODEL
    assert claim.model_info is not None
    assert claim.model_info.is_mock is True
    assert len(claim.evidence_ids) == 1
    assert claim.evidence_ids[0] == result.evidence.id

    # 3. Evidence artifact integrity
    store = EvidenceStore(run_dir)
    assert store.get(result.evidence.id) is not None
    assert store.verify(result.evidence).valid
    ev_file = run_dir / "evidence" / result.evidence.path
    assert ev_file.exists()
    content = ev_file.read_bytes()
    expected_sha = hashlib.sha256(content).hexdigest()
    assert result.evidence.sha256 == expected_sha
    assert result.evidence.kind == EvidenceKind.MODEL_RESPONSE

    # 4. Check proposal.json artifact was written
    proposal_json_file = run_dir / "proposal.json"
    assert proposal_json_file.exists()
    saved_data = json.loads(proposal_json_file.read_text(encoding="utf-8"))
    assert saved_data["target_dependency"] == "appdirs"
    assert saved_data["proposal"]["proposed_package"] == "platformdirs"

    # 5. Tree immutability check (Rule 5)
    fp_after = tree_fingerprint(synthetic_target)
    assert fp_before == fp_after


def test_run_propose_jinja2_upgrade(synthetic_target: Path, tmp_path: Path) -> None:
    """run_propose on jinja2 proposes UPGRADE within Jinja2."""
    run_dir = tmp_path / "jinja2_run"
    result = run_propose(
        repo_path=synthetic_target,
        dep_name="jinja2",
        backend="mock",
        run_dir=run_dir,
    )

    assert result.proposal.kind == ProposalKind.UPGRADE
    assert result.proposal.proposed_package == "Jinja2"
    assert ">=3" in result.proposal.proposed_specifier
    assert result.claim.trust_level == TrustLevel.AI_PROPOSED


def test_run_propose_nonexistent_repo() -> None:
    """run_propose raises FileNotFoundError when repo path is invalid."""
    with pytest.raises(FileNotFoundError):
        run_propose(
            repo_path="/path/to/nonexistent/repo",
            dep_name="appdirs",
            backend="mock",
        )
