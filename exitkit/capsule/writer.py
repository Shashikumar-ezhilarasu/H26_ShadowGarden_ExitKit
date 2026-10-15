"""Dependency Exit Capsule bundle builder and serialization."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from exitkit import __version__
from exitkit.compare.models import ComparisonReport
from exitkit.ledger.models import (
    BaselineOutcome,
    BaselineRun,
    Capsule,
    Claim,
    CompatibilityGap,
    DependencyInfo,
    EnvironmentInfo,
    Evidence,
    ExecutedResult,
    MigrationStep,
    ModelInfo,
    Proposal,
    ProposalKind,
    RunLabel,
    TargetInfo,
)
from exitkit.ledger.store import EvidenceStore
from exitkit.sandbox.models import SandboxVerificationResult
from exitkit.util.fingerprint import tree_fingerprint


def _hash_file(path: Path) -> str:
    """Compute sha256 hex digest of a file on disk."""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def _render_summary_html(capsule: Capsule) -> str:
    """Generate a clean self-contained HTML report summary for the capsule."""
    claims_rows = "".join(
        f"<tr><td><code>{c.id}</code></td><td>{c.trust_level.value}</td>"
        f"<td>{c.statement}</td><td>{', '.join(c.evidence_ids) or 'N/A'}</td></tr>"
        for c in capsule.claims
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>ExitKit Capsule Report - {capsule.dependency.name}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
           margin: 40px; color: #222; background: #fafafa; }}
    .container {{ max-width: 900px; margin: auto; background: white; padding: 30px;
                  border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
    h1 {{ color: #111; margin-bottom: 5px; }}
    .badge {{ display: inline-block; padding: 4px 10px; border-radius: 4px;
              font-weight: bold; background: #e0f2fe; color: #0369a1; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
    th, td {{ padding: 10px; border-bottom: 1px solid #e5e7eb; text-align: left; font-size: 14px; }}
    th {{ background: #f9fafb; font-weight: 600; }}
  </style>
</head>
<body>
  <div class="container">
    <h1>Dependency Exit Capsule: {capsule.dependency.name}</h1>
    <p>Capsule ID: <code>{capsule.capsule_id}</code></p>
    <p>Created: {capsule.created_at.isoformat()} UTC</p>
    <div>
      <span class="badge">ExitKit v{capsule.exitkit_version}</span>
      <span class="badge">Target: {capsule.target.repo_ref}</span>
      <span class="badge">Synthetic: {capsule.synthetic}</span>
    </div>
    <h2>Sacred Trust Ledger Claims ({len(capsule.claims)})</h2>
    <table>
      <thead>
        <tr><th>Claim ID</th><th>Trust Level</th><th>Statement</th><th>Evidence IDs</th></tr>
      </thead>
      <tbody>
        {claims_rows}
      </tbody>
    </table>
  </div>
</body>
</html>"""


def build_capsule(
    run_dir: Path | str,
    target_repo: Path | str | None = None,
    dep_name: str | None = None,
    output_dir: Path | str | None = None,
    archive: bool = False,
) -> Path:
    """Compile artifacts from a run directory into a validated Dependency Exit Capsule."""
    run_path = Path(run_dir).resolve()
    if not run_path.exists():
        raise FileNotFoundError(f"Run directory not found: {run_path}")

    store = EvidenceStore(run_path)
    evidence_dict: dict[str, Evidence] = {ev.id: ev for ev in store.all()}

    # 1. Load verification.json if present
    verification: SandboxVerificationResult | None = None
    v_file = run_path / "verification.json"
    if v_file.exists():
        verification = SandboxVerificationResult.model_validate_json(
            v_file.read_text(encoding="utf-8")
        )
        for ev in verification.evidence_items:
            evidence_dict[ev.id] = ev

    # Load proposal.json if present
    p_file = run_path / "proposal.json"
    if p_file.exists():
        try:
            prop_data = json.loads(p_file.read_text(encoding="utf-8"))
            if "evidence" in prop_data:
                ev_obj = Evidence.model_validate(prop_data["evidence"])
                evidence_dict[ev_obj.id] = ev_obj
        except Exception:
            pass

    all_evidence = list(evidence_dict.values())

    # 2. Load comparison.json if present
    comparison: ComparisonReport | None = None
    c_file = run_path / "comparison.json"
    if c_file.exists():
        comparison = ComparisonReport.model_validate_json(c_file.read_text(encoding="utf-8"))

    # Determine dependency name
    active_dep = (
        dep_name
        or (verification.target_dependency if verification else None)
        or (comparison.target_dependency if comparison else None)
        or "unknown"
    )

    # 3. Collect claims and detect synthetic (mock) usage
    claims_dict: dict[str, Claim] = {}
    is_synthetic = False

    if verification:
        if verification.proposal_claim:
            claims_dict[verification.proposal_claim.id] = verification.proposal_claim
            if (
                verification.proposal_claim.model_info
                and verification.proposal_claim.model_info.is_mock
            ):
                is_synthetic = True
        if verification.resulting_claim:
            claims_dict[verification.resulting_claim.id] = verification.resulting_claim

    if comparison:
        for tr in comparison.triages:
            claims_dict[tr.claim.id] = tr.claim
            if tr.claim.model_info and tr.claim.model_info.is_mock:
                is_synthetic = True

    claims = list(claims_dict.values())

    # 4. Construct Executed Results
    executed_results: list[ExecutedResult] = []
    baseline_run = BaselineRun(outcome=BaselineOutcome.NOT_RUN)
    patch_content = ""

    if verification:
        patch_content = verification.unified_patch or ""
        py_ver = verification.python_version

        # Baseline
        base_claim = verification.resulting_claim.id if verification.resulting_claim else None
        executed_results.append(
            ExecutedResult(
                label=RunLabel.BASELINE,
                python_version=py_ver,
                passed=verification.baseline.passed_count,
                failed=verification.baseline.failed_count,
                errors=verification.baseline.error_count,
                skipped=verification.baseline.skipped_count,
                duration_seconds=verification.baseline.duration_seconds,
                exit_code=verification.baseline.exit_code,
                evidence_id=verification.baseline.evidence_id,
                claim_id=base_claim,
            )
        )
        baseline_run = BaselineRun(
            outcome=verification.baseline.baseline_outcome or BaselineOutcome.PASS_AS_DECLARED,
            evidence_ids=[verification.baseline.evidence_id],
            claim_ids=[base_claim] if base_claim else [],
        )

        # Migrated
        executed_results.append(
            ExecutedResult(
                label=RunLabel.MIGRATED,
                python_version=py_ver,
                passed=verification.migrated.passed_count,
                failed=verification.migrated.failed_count,
                errors=verification.migrated.error_count,
                skipped=verification.migrated.skipped_count,
                duration_seconds=verification.migrated.duration_seconds,
                exit_code=verification.migrated.exit_code,
                evidence_id=verification.migrated.evidence_id,
                claim_id=base_claim,
            )
        )

        # Differential
        diff = verification.differential
        diff_failed = diff.total_cases - diff.matched_cases
        executed_results.append(
            ExecutedResult(
                label=RunLabel.DIFFERENTIAL,
                python_version=py_ver,
                passed=diff.matched_cases,
                failed=diff_failed,
                errors=0,
                skipped=0,
                duration_seconds=diff.duration_seconds,
                exit_code=0 if diff.all_matched else 1,
                evidence_id=diff.evidence_id,
                claim_id=base_claim,
            )
        )

    # 5. Build Proposal model
    prop_obj: Proposal | None = None
    if verification:
        prop_claim_id = verification.proposal_claim.id
        replacement_pkg = verification.differential.replacement_dependency
        is_upgrade = replacement_pkg.lower() == active_dep.lower()
        prop_obj = Proposal(
            kind=ProposalKind.UPGRADE if is_upgrade else ProposalKind.REPLACE,
            replacement_name=replacement_pkg,
            rationale_claim_ids=[prop_claim_id],
            migration_steps=[
                MigrationStep(
                    order=1,
                    description=f"Migrate {active_dep} to {replacement_pkg}",
                    claim_id=prop_claim_id,
                )
            ],
        )

    # 6. Gaps and limitations from comparison
    capsule_gaps: list[CompatibilityGap] = []
    limitations: list[str] = []
    if comparison:
        for g in comparison.gaps:
            tr_claim = next((t.claim for t in comparison.triages if t.gap_id == g.id), None)
            cid = tr_claim.id if tr_claim else claims[0].id
            capsule_gaps.append(
                CompatibilityGap(
                    description=g.description,
                    severity=g.severity.value,
                    claim_id=cid,
                )
            )
        for lim in comparison.limitations:
            limitations.append(f"{lim.category.value}: {lim.description}")

    # 7. Environment & Target Info
    effective_target_repo = target_repo or (
        verification.target_repo if verification else str(run_path)
    )
    target_path = Path(effective_target_repo)
    is_valid_dir = target_path.exists() and target_path.is_dir()
    tree_fp = tree_fingerprint(target_path) if is_valid_dir else "synthetic"

    top_model: ModelInfo | None = None
    if claims:
        top_model = next((c.model_info for c in claims if c.model_info is not None), None)

    capsule = Capsule(
        schema_version="1.0",
        capsule_id=f"cap-{uuid4().hex[:12]}",
        created_at=datetime.now(UTC),
        exitkit_version=__version__,
        synthetic=is_synthetic,
        environment=EnvironmentInfo(
            python_version=sys.version.split()[0],
            platform=sys.platform,
        ),
        target=TargetInfo(
            repo_ref=str(effective_target_repo),
            tree_fingerprint=tree_fp,
        ),
        dependency=DependencyInfo(name=active_dep),
        baseline=baseline_run,
        proposal=prop_obj,
        executed_results=executed_results,
        compatibility_gaps=capsule_gaps,
        limitations=limitations,
        model_info=top_model,
        claims=claims,
        evidence=all_evidence,
    )

    # 8. Output directory preparation
    effective_out = Path(output_dir) if output_dir else run_path / f"capsule_{capsule.capsule_id}"
    capsule_dir = effective_out if not archive else run_path / f"_tmp_{capsule.capsule_id}"
    if capsule_dir.exists():
        shutil.rmtree(capsule_dir)
    capsule_dir.mkdir(parents=True)

    # Copy evidence directory
    out_ev_dir = capsule_dir / "evidence"
    src_ev_dir = run_path / "evidence"
    if src_ev_dir.exists():
        shutil.copytree(src_ev_dir, out_ev_dir)
    else:
        out_ev_dir.mkdir()

    # Write patch
    patch_dir = capsule_dir / "patch"
    patch_dir.mkdir()
    (patch_dir / "migration.patch").write_text(patch_content, encoding="utf-8")

    # Write report.html
    html_content = _render_summary_html(capsule)
    (capsule_dir / "report.html").write_text(html_content, encoding="utf-8")

    # Write canonical capsule.json
    capsule_json_file = capsule_dir / "capsule.json"
    capsule_json_file.write_bytes(capsule.to_canonical_json().encode("utf-8"))

    # Write SHA256SUMS manifest of all payload files
    manifest_entries: list[str] = []
    for file_path in sorted(capsule_dir.rglob("*")):
        if file_path.is_file() and file_path.name != "SHA256SUMS":
            rel_path = file_path.relative_to(capsule_dir).as_posix()
            file_sha = _hash_file(file_path)
            manifest_entries.append(f"{file_sha}  {rel_path}\n")

    sums_file = capsule_dir / "SHA256SUMS"
    sums_file.write_text("".join(manifest_entries), encoding="utf-8")

    if archive:
        is_tar = str(effective_out).endswith(".tar.gz")
        tar_target = effective_out if is_tar else effective_out.with_suffix(".tar.gz")
        with tarfile.open(tar_target, "w:gz") as tar:
            for item in capsule_dir.iterdir():
                tar.add(item, arcname=item.name)
        shutil.rmtree(capsule_dir)
        return tar_target

    return capsule_dir
