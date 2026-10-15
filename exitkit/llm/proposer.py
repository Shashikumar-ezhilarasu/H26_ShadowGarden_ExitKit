"""Proposer orchestrator formulating evidence-backed, bounded migration plans."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal
from uuid import uuid4

from packaging.utils import canonicalize_name

from exitkit.discover.manifests import discover_dependencies
from exitkit.discover.models import DiscoveredDependency
from exitkit.ledger.models import Claim, ClaimSource, EvidenceKind, RiskSignal, TrustLevel
from exitkit.ledger.store import EvidenceStore
from exitkit.llm.client import BaseModelClient, get_model_client
from exitkit.llm.models import ProposerResult
from exitkit.llm.prompts import build_proposer_prompt
from exitkit.scan.risk import EvaluatedRisk
from exitkit.usage.analyzer import map_repository_usage
from exitkit.util.fingerprint import tree_fingerprint


def _find_recent_risk_signal(target_canonical: str, dep_name: str) -> EvaluatedRisk | None:
    """Attempt to locate cached or recent risk evaluation for the dependency."""
    runs_dir = Path(".exitkit/runs")
    if not runs_dir.exists():
        return None

    for run_path in sorted(runs_dir.iterdir(), reverse=True):
        if not run_path.is_dir():
            continue
        scan_file = run_path / "scan.json"
        if scan_file.exists():
            try:
                scan_data = json.loads(scan_file.read_text(encoding="utf-8"))
                for s_dep in scan_data.get("scanned_dependencies", []):
                    d_info = s_dep.get("dependency", {})
                    if (
                        d_info.get("canonical_name") == target_canonical
                        or d_info.get("name", "").lower() == dep_name.lower()
                    ):
                        r_info = s_dep.get("risk", {})
                        signals = [
                            RiskSignal.model_validate(sig) for sig in r_info.get("signals", [])
                        ]
                        return EvaluatedRisk(
                            dependency_name=dep_name,
                            risk_score=r_info.get("risk_score", 0),
                            signals=signals,
                            claims=[],
                            resolved_version=r_info.get("resolved_version"),
                        )
            except Exception:
                continue
    return None


def run_propose(
    repo_path: Path | str,
    dep_name: str,
    model_id: str | None = None,
    backend: Literal["google", "ollama", "mock"] | None = None,
    client: BaseModelClient | None = None,
    run_dir: Path | str | None = None,
    risk: EvaluatedRisk | None = None,
) -> ProposerResult:
    """Run the bounded proposer to generate an AI-proposed migration plan."""
    root = Path(repo_path).resolve()
    if not root.exists():
        raise FileNotFoundError(f"Target repository path does not exist: {root}")

    # Invariant: Record fingerprint before and after (Rule 5)
    fp_before = tree_fingerprint(root)

    # 1. Initialize EvidenceStore for this proposer run
    effective_run_dir = Path(run_dir) if run_dir else EvidenceStore.create_run_dir()
    store = EvidenceStore(effective_run_dir)

    # 2. Discover dependency declarations
    disc = discover_dependencies(root)
    target_canonical = canonicalize_name(dep_name)
    matched_dep: DiscoveredDependency | None = None
    for d in disc.dependencies:
        if d.canonical_name == target_canonical:
            matched_dep = d
            break

    if not matched_dep:
        matched_dep = DiscoveredDependency(
            name=dep_name,
            canonical_name=target_canonical,
        )

    # 3. Map AST code usage
    usage_report = map_repository_usage(root, target_dep=dep_name)
    dep_usage = usage_report.usages.get(target_canonical)

    # 4. Resolve risk signals if not directly passed
    resolved_risk = risk or _find_recent_risk_signal(target_canonical, dep_name)

    # 5. Construct prompt
    prompt = build_proposer_prompt(
        dep=matched_dep,
        risk=resolved_risk,
        usage=dep_usage,
    )

    # 5. Invoke LLM client
    active_client = client or get_model_client(backend=backend, model_id=model_id)
    proposal, response_bytes, model_info = active_client.generate_proposal(
        target_dep=dep_name,
        prompt=prompt,
    )

    # 6. Save response as cryptographic evidence
    ev_desc = (
        f"Gemma migration proposal for {dep_name} via {model_info.backend} ({model_info.model_id})"
    )
    ev = store.add_bytes(
        kind=EvidenceKind.MODEL_RESPONSE,
        content=response_bytes,
        description=ev_desc,
        ext="json",
    )

    # 7. Generate sacred AI_PROPOSED claim
    claim_id = f"claim-proposal-{target_canonical}-{uuid4().hex[:8]}"
    claim_stmt = (
        f"Proposed migration for {dep_name}: {proposal.kind.value} to "
        f"{proposal.proposed_package} {proposal.proposed_specifier}"
    )
    claim = Claim(
        id=claim_id,
        statement=claim_stmt,
        trust_level=TrustLevel.AI_PROPOSED,
        source=ClaimSource.MODEL,
        evidence_ids=[ev.id],
        model_info=model_info,
    )

    # 8. Persist structured proposal.json into run directory
    proposal_path = effective_run_dir / "proposal.json"
    proposal_payload = {
        "target_dependency": dep_name,
        "target_repo": str(root),
        "proposal": proposal.model_dump(mode="json"),
        "claim": claim.model_dump(mode="json"),
        "evidence": ev.model_dump(mode="json"),
        "model_info": model_info.model_dump(mode="json"),
    }
    proposal_path.write_text(json.dumps(proposal_payload, indent=2), encoding="utf-8")

    # 9. Invariant check: target repo was untouched
    fp_after = tree_fingerprint(root)
    if fp_before != fp_after:
        raise RuntimeError(
            f"Target repository safety violation: tree fingerprint changed during propose! "
            f"Before: {fp_before}, After: {fp_after}"
        )

    return ProposerResult(
        target_dependency=dep_name,
        target_repo=str(root),
        proposal=proposal,
        claim=claim,
        evidence=ev,
        model_info=model_info,
        run_dir=str(effective_run_dir),
    )
