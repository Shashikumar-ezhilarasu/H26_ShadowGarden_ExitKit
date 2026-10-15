"""Scanner orchestrator combining discovery, evidence collection, and risk evaluation."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from packaging.utils import canonicalize_name

from exitkit.discover.manifests import discover_dependencies
from exitkit.discover.models import DependencyScope, DiscoveredDependency, ManifestDivergence
from exitkit.ledger.integrity import Problem
from exitkit.ledger.models import Claim
from exitkit.ledger.store import EvidenceStore
from exitkit.scan.cache import NetworkCache, resolve_target_repo
from exitkit.scan.clients import EvidenceHttpClient
from exitkit.scan.risk import EvaluatedRisk, evaluate_dependency_risk
from exitkit.scan.smoke import run_repo_dry_run_resolver, run_smoke_test
from exitkit.util.fingerprint import tree_fingerprint

if TYPE_CHECKING:
    import httpx


@dataclass(frozen=True)
class ScannedDependency:
    """Summary of a scanned dependency with its evaluated signals."""

    dependency: DiscoveredDependency
    risk: EvaluatedRisk
    py312_import_ok: bool | None
    py313_import_ok: bool | None
    error_summary: str | None


@dataclass(frozen=True)
class ScanResult:
    """Overall result of a repository risk scan."""

    target_path: str
    is_remote: bool
    tree_fingerprint: str
    run_dir: Path
    scanned_dependencies: list[ScannedDependency]
    commented_out_pins: list[str]
    limitations: list[str]
    divergences: list[ManifestDivergence]
    evidence_count: int
    claims_count: int
    verification_problems: list[Problem]

    def to_dict(self) -> dict[str, object]:
        """Convert scan result to JSON serializable dictionary."""
        return {
            "target_path": self.target_path,
            "is_remote": self.is_remote,
            "tree_fingerprint": self.tree_fingerprint,
            "run_dir": str(self.run_dir),
            "dependencies": [
                {
                    "name": s.dependency.name,
                    "canonical_name": s.dependency.canonical_name,
                    "scope": s.dependency.primary_scope.value,
                    "primary_specifier": s.dependency.primary_specifier,
                    "resolved_version": s.risk.resolved_version,
                    "risk_score": s.risk.risk_score,
                    "py312_import_ok": s.py312_import_ok,
                    "py313_import_ok": s.py313_import_ok,
                    "error_summary": s.error_summary,
                    "signals": [
                        {
                            "name": sig.name,
                            "status": sig.status.value,
                            "points": sig.points,
                            "detail": sig.detail,
                            "claim_id": sig.claim_id,
                            "evidence_ids": sig.evidence_ids,
                        }
                        for sig in s.risk.signals
                    ],
                }
                for s in self.scanned_dependencies
            ],
            "commented_out_pins": self.commented_out_pins,
            "limitations": self.limitations,
            "divergences": [
                {
                    "files": d.files,
                    "specifiers": d.specifiers,
                    "compatible": d.compatible,
                    "detail": d.detail,
                }
                for d in self.divergences
            ],
            "evidence_count": self.evidence_count,
            "claims_count": self.claims_count,
            "verification_status": (
                "VALID" if not self.verification_problems else "TAMPERED_OR_CORRUPT"
            ),
        }


def run_scan(
    repo_path_or_url: str,
    only: list[str] | None = None,
    use_cache: bool = True,
    offline: bool = False,
    timeout: float = 180.0,
    concurrency: int = 8,
    out_dir: Path | str | None = None,
    http_transport: httpx.BaseTransport | None = None,
) -> ScanResult:
    """Execute a comprehensive, evidence-backed dependency risk scan on a repository."""
    target_path, is_remote = resolve_target_repo(repo_path_or_url)

    # 1. Compute tree fingerprint before any operation
    fingerprint_before = tree_fingerprint(target_path)

    # 2. Setup EvidenceStore run directory
    run_dir = Path(out_dir).resolve() if out_dir else EvidenceStore.create_run_dir()
    store = EvidenceStore(run_dir)
    cache = NetworkCache()
    http_client = EvidenceHttpClient(
        evidence_store=store,
        cache=cache,
        use_cache=use_cache,
        offline=offline,
        timeout=min(30.0, timeout),
        transport=http_transport,
    )

    # 3. Discover all direct dependencies
    discovery = discover_dependencies(target_path)
    deps_to_scan = discovery.dependencies

    if only:
        allowed = {canonicalize_name(n) for n in only}
        deps_to_scan = [d for d in deps_to_scan if d.canonical_name in allowed]

    # 4. Repo-level resolver dry-run for primary runtime requirements
    runtime_reqs: list[str] = []
    for d in discovery.dependencies:
        if d.is_pypi and d.primary_scope == DependencyScope.RUNTIME:
            runtime_reqs.append(f"{d.name}{d.primary_specifier}")

    run_repo_dry_run_resolver(runtime_reqs, "3.12", store, timeout=timeout)
    run_repo_dry_run_resolver(runtime_reqs, "3.13", store, timeout=timeout)

    # 5. Process dependencies
    scanned_list: list[ScannedDependency] = []
    all_claims: list[Claim] = []

    def process_dependency(dep: DiscoveredDependency) -> ScannedDependency:
        # Non-PyPI short circuit
        if not dep.is_pypi:
            risk = evaluate_dependency_risk(dep, None, None, None, None, None)
            return ScannedDependency(
                dependency=dep,
                risk=risk,
                py312_import_ok=None,
                py313_import_ok=None,
                error_summary="Non-PyPI dependency",
            )

        # PyPI metadata fetch
        pypi_data = http_client.fetch_pypi(dep.name, dep.resolved_version)

        # GitHub metadata fetch
        github_data = None
        if pypi_data.github_repo:
            owner, repo = pypi_data.github_repo
            github_data = http_client.fetch_github(owner, repo)

        # Isolated smoke tests on Python 3.12 and 3.13
        smoke_312 = run_smoke_test(dep.name, dep.primary_specifier, "3.12", store, timeout=timeout)
        smoke_313 = run_smoke_test(dep.name, dep.primary_specifier, "3.13", store, timeout=timeout)

        # OSV vulnerabilities
        resolved_candidates = (
            dep.resolved_version,
            smoke_312.installed_version,
            smoke_313.installed_version,
        )
        ver_for_osv = next((v for v in resolved_candidates if v), None)
        osv_data = http_client.fetch_osv(dep.name, ver_for_osv)

        # Risk scoring
        risk = evaluate_dependency_risk(dep, pypi_data, github_data, smoke_312, smoke_313, osv_data)

        # Aggregate import status
        py312_ok = smoke_312.import_passed if smoke_312.install_passed else False
        py313_ok = smoke_313.import_passed if smoke_313.install_passed else False
        err = smoke_312.error_summary or smoke_313.error_summary

        return ScannedDependency(
            dependency=dep,
            risk=risk,
            py312_import_ok=py312_ok,
            py313_import_ok=py313_ok,
            error_summary=err,
        )

    # Concurrency limit for scanning dependencies
    max_workers = max(1, min(concurrency, len(deps_to_scan) or 1))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_dependency, d): d for d in deps_to_scan}
        for future in as_completed(futures):
            scanned_dep = future.result()
            scanned_list.append(scanned_dep)
            all_claims.extend(scanned_dep.risk.claims)

    # Sort descending by risk score, then alphabetically
    scanned_list.sort(key=lambda s: (-s.risk.risk_score, s.dependency.canonical_name))

    # 6. Verify target repository was completely untouched (Rule 5)
    fingerprint_after = tree_fingerprint(target_path)
    if fingerprint_before != fingerprint_after:
        raise RuntimeError(
            f"Target repository safety violation: tree fingerprint changed during scan! "
            f"Before: {fingerprint_before}, After: {fingerprint_after}"
        )

    # 7. Collect divergences from all dependencies
    divergences = [d.divergence for d in discovery.dependencies if d.divergence is not None]

    # 8. Cryptographic verification of all stored evidence
    problems = store.verify_all()

    result = ScanResult(
        target_path=str(target_path),
        is_remote=is_remote,
        tree_fingerprint=fingerprint_before,
        run_dir=run_dir,
        scanned_dependencies=scanned_list,
        commented_out_pins=discovery.commented_out_pins,
        limitations=discovery.limitations,
        divergences=divergences,
        evidence_count=len(store._registry),
        claims_count=len(all_claims),
        verification_problems=problems,
    )

    # Persist scan.json in the run directory
    scan_json_file = run_dir / "scan.json"
    scan_json_file.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")

    return result
