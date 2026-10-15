"""Transparent and deterministic risk scoring engine for dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from packaging.version import InvalidVersion, Version

from exitkit.discover.models import DiscoveredDependency
from exitkit.ledger.models import Claim, ClaimSource, RiskSignal, SignalStatus, TrustLevel

if TYPE_CHECKING:
    from exitkit.scan.clients import GitHubData, OsvData, PypiData
    from exitkit.scan.smoke import SmokeResult

# --- Risk Signal Point Constants ---
POINTS_IMPORT_FAILS_PY312 = 35
POINTS_IMPORT_FAILS_PY313 = 35
POINTS_INSTALL_FAILS_PY312 = 30
POINTS_INSTALL_FAILS_PY313 = 30
POINTS_YANKED_VERSION = 25
POINTS_REPO_ARCHIVED = 25
POINTS_INACTIVE_CLASSIFIER = 25
POINTS_INCOMPATIBLE_DIVERGENCE = 20
POINTS_COMPATIBLE_DIVERGENCE = 5
POINTS_NO_PY312_OR_PY313 = 15
POINTS_STALE_LATEST_RELEASE = 15
POINTS_PINNED_VERSION_OLD = 10
POINTS_PINNED_BEHIND_LATEST = 10
MAX_VULNERABILITY_POINTS = 40
POINTS_NON_PYPI = 0

STALE_THRESHOLD_DAYS = 1095  # 3 years


@dataclass(frozen=True)
class EvaluatedRisk:
    """Calculated risk score, signals, and associated ledger claims for a dependency."""

    dependency_name: str
    risk_score: int
    signals: list[RiskSignal]
    claims: list[Claim]
    resolved_version: str | None


def _make_signal_and_claim(
    name: str,
    points: int,
    detail: str,
    status: SignalStatus,
    evidence_ids: list[str],
    unavailable_reason: str | None = None,
) -> tuple[RiskSignal, Claim]:
    """Helper to produce a RiskSignal and its strictly coupled ledger Claim."""
    claim_id = f"claim-sig-{name.lower()}-{uuid4().hex[:8]}"

    if status == SignalStatus.UNAVAILABLE:
        note = unavailable_reason or f"Signal {name} was unavailable during scan"
        claim = Claim(
            id=claim_id,
            statement=f"{name}: {detail}",
            trust_level=TrustLevel.UNVERIFIED,
            source=ClaimSource.TOOL,
            note=note,
            evidence_ids=[],
        )
        signal = RiskSignal(
            name=name,
            status=SignalStatus.UNAVAILABLE,
            points=0,
            detail=f"{detail} ({note})",
            claim_id=claim_id,
            evidence_ids=[],
        )
        return signal, claim

    claim = Claim(
        id=claim_id,
        statement=f"{name}: {detail}",
        trust_level=TrustLevel.OBSERVED,
        source=ClaimSource.TOOL,
        evidence_ids=list(evidence_ids),
    )
    signal = RiskSignal(
        name=name,
        status=SignalStatus.AVAILABLE,
        points=points,
        detail=detail,
        claim_id=claim_id,
        evidence_ids=list(evidence_ids),
    )
    return signal, claim


def evaluate_dependency_risk(
    dep: DiscoveredDependency,
    pypi_data: PypiData | None,
    github_data: GitHubData | None,
    smoke_312: SmokeResult | None,
    smoke_313: SmokeResult | None,
    osv_data: OsvData | None,
) -> EvaluatedRisk:
    """Evaluate all risk signals for a dependency and compute total risk score."""
    signals: list[RiskSignal] = []
    claims: list[Claim] = []

    # Non-PyPI Source
    if not dep.is_pypi:
        sig, cl = _make_signal_and_claim(
            name="NON_PYPI_SOURCE",
            points=POINTS_NON_PYPI,
            detail=f"Dependency points to non-PyPI source: {dep.url_or_vcs or 'local/vcs'}",
            status=SignalStatus.UNAVAILABLE,
            evidence_ids=[],
            unavailable_reason="Non-PyPI dependencies cannot be queried against PyPI or OSV",
        )
        signals.append(sig)
        claims.append(cl)
        return EvaluatedRisk(
            dependency_name=dep.name,
            risk_score=0,
            signals=signals,
            claims=claims,
            resolved_version=dep.resolved_version,
        )

    # 1. Manifest Divergence
    if dep.divergence is not None:
        div = dep.divergence
        if not div.compatible:
            pts = POINTS_INCOMPATIBLE_DIVERGENCE
            spec_pairs = ", ".join(
                f"{f}: {s}" for f, s in zip(div.files, div.specifiers, strict=False)
            )
            det = f"Incompatible specifiers across manifests: {spec_pairs} - {div.detail}"
        else:
            pts = POINTS_COMPATIBLE_DIVERGENCE
            det = f"Divergent but compatible specifiers: {', '.join(div.specifiers)}"

        # Primary specifier declaration is evidence of divergence
        sig, cl = _make_signal_and_claim(
            name="MANIFEST_DIVERGENCE",
            points=pts,
            detail=det,
            status=SignalStatus.AVAILABLE,
            evidence_ids=[pypi_data.evidence.id] if pypi_data and pypi_data.evidence else [],
        )
        signals.append(sig)
        claims.append(cl)

    # 2. Smoke Tests on Python 3.12
    if smoke_312 is not None:
        if smoke_312.status == SignalStatus.UNAVAILABLE:
            sig, cl = _make_signal_and_claim(
                name="SMOKE_PY312",
                points=0,
                detail="Python 3.12 smoke test unavailable",
                status=SignalStatus.UNAVAILABLE,
                evidence_ids=[],
                unavailable_reason=smoke_312.unavailable_reason,
            )
            signals.append(sig)
            claims.append(cl)
        else:
            if not smoke_312.install_passed and smoke_312.install_evidence:
                sig, cl = _make_signal_and_claim(
                    name="INSTALL_FAILS_PY312",
                    points=POINTS_INSTALL_FAILS_PY312,
                    detail=f"Installation failed on Python 3.12: {smoke_312.error_summary}",
                    status=SignalStatus.AVAILABLE,
                    evidence_ids=[smoke_312.install_evidence.id],
                )
                signals.append(sig)
                claims.append(cl)
            elif not smoke_312.import_passed and smoke_312.import_evidence:
                sig, cl = _make_signal_and_claim(
                    name="IMPORT_FAILS_PY312",
                    points=POINTS_IMPORT_FAILS_PY312,
                    detail=f"Import failed on Python 3.12: {smoke_312.error_summary}",
                    status=SignalStatus.AVAILABLE,
                    evidence_ids=[smoke_312.import_evidence.id],
                )
                signals.append(sig)
                claims.append(cl)

    # 3. Smoke Tests on Python 3.13
    if smoke_313 is not None:
        if smoke_313.status == SignalStatus.UNAVAILABLE:
            sig, cl = _make_signal_and_claim(
                name="SMOKE_PY313",
                points=0,
                detail="Python 3.13 smoke test unavailable",
                status=SignalStatus.UNAVAILABLE,
                evidence_ids=[],
                unavailable_reason=smoke_313.unavailable_reason,
            )
            signals.append(sig)
            claims.append(cl)
        else:
            if not smoke_313.install_passed and smoke_313.install_evidence:
                sig, cl = _make_signal_and_claim(
                    name="INSTALL_FAILS_PY313",
                    points=POINTS_INSTALL_FAILS_PY313,
                    detail=f"Installation failed on Python 3.13: {smoke_313.error_summary}",
                    status=SignalStatus.AVAILABLE,
                    evidence_ids=[smoke_313.install_evidence.id],
                )
                signals.append(sig)
                claims.append(cl)
            elif not smoke_313.import_passed and smoke_313.import_evidence:
                sig, cl = _make_signal_and_claim(
                    name="IMPORT_FAILS_PY313",
                    points=POINTS_IMPORT_FAILS_PY313,
                    detail=f"Import failed on Python 3.13: {smoke_313.error_summary}",
                    status=SignalStatus.AVAILABLE,
                    evidence_ids=[smoke_313.import_evidence.id],
                )
                signals.append(sig)
                claims.append(cl)

    # Resolved version resolution (from lockfile or smoke test)
    resolved_ver = (
        dep.resolved_version
        or (smoke_312.installed_version if smoke_312 and smoke_312.installed_version else None)
        or (smoke_313.installed_version if smoke_313 and smoke_313.installed_version else None)
    )

    # 4. PyPI Signals
    if pypi_data is not None:
        if pypi_data.status == SignalStatus.UNAVAILABLE:
            sig, cl = _make_signal_and_claim(
                name="PYPI_METADATA",
                points=0,
                detail="PyPI metadata unavailable",
                status=SignalStatus.UNAVAILABLE,
                evidence_ids=[],
                unavailable_reason=pypi_data.unavailable_reason,
            )
            signals.append(sig)
            claims.append(cl)
        elif pypi_data.evidence:
            ev_id = pypi_data.evidence.id

            # Yanked version
            if pypi_data.is_yanked:
                target_str = resolved_ver or dep.primary_specifier
                sig, cl = _make_signal_and_claim(
                    name="YANKED_VERSION_IN_USE",
                    points=POINTS_YANKED_VERSION,
                    detail=f"Version is marked yanked on PyPI: {target_str}",
                    status=SignalStatus.AVAILABLE,
                    evidence_ids=[ev_id],
                )
                signals.append(sig)
                claims.append(cl)

            # Inactive classifier
            if pypi_data.is_inactive:
                sig, cl = _make_signal_and_claim(
                    name="INACTIVE_CLASSIFIER",
                    points=POINTS_INACTIVE_CLASSIFIER,
                    detail="PyPI declares 'Development Status :: 7 - Inactive'",
                    status=SignalStatus.AVAILABLE,
                    evidence_ids=[ev_id],
                )
                signals.append(sig)
                claims.append(cl)

            # Python 3.12 / 3.13 support
            if not pypi_data.declares_py312 and not pypi_data.declares_py313:
                sig, cl = _make_signal_and_claim(
                    name="NO_PY312_OR_PY313_SUPPORT",
                    points=POINTS_NO_PY312_OR_PY313,
                    detail="Package does not declare Python 3.12 or 3.13 classifiers on PyPI",
                    status=SignalStatus.AVAILABLE,
                    evidence_ids=[ev_id],
                )
                signals.append(sig)
                claims.append(cl)

            # Stale latest release
            now = datetime.now(UTC)
            stale_cutoff = now - timedelta(days=STALE_THRESHOLD_DAYS)
            if pypi_data.latest_release_date and pypi_data.latest_release_date < stale_cutoff:
                age_days = (now - pypi_data.latest_release_date).days
                stale_detail = (
                    f"Latest PyPI release was {age_days} days ago ({pypi_data.latest_version})"
                )
                sig, cl = _make_signal_and_claim(
                    name="STALE_LATEST_RELEASE",
                    points=POINTS_STALE_LATEST_RELEASE,
                    detail=stale_detail,
                    status=SignalStatus.AVAILABLE,
                    evidence_ids=[ev_id],
                )
                signals.append(sig)
                claims.append(cl)

            # Old pinned release
            if pypi_data.target_version_date and pypi_data.target_version_date < stale_cutoff:
                age_days = (now - pypi_data.target_version_date).days
                sig, cl = _make_signal_and_claim(
                    name="PINNED_VERSION_OLD",
                    points=POINTS_PINNED_VERSION_OLD,
                    detail=f"Pinned release is {age_days} days old",
                    status=SignalStatus.AVAILABLE,
                    evidence_ids=[ev_id],
                )
                signals.append(sig)
                claims.append(cl)

            # Pinned behind latest major
            if resolved_ver and pypi_data.latest_version:
                try:
                    v_res = Version(resolved_ver)
                    v_lat = Version(pypi_data.latest_version)
                    if v_lat.major > v_res.major:
                        behind_detail = (
                            f"Version in use ({resolved_ver}) is major version(s) behind latest "
                            f"({pypi_data.latest_version})"
                        )
                        sig, cl = _make_signal_and_claim(
                            name="PINNED_BEHIND_LATEST",
                            points=POINTS_PINNED_BEHIND_LATEST,
                            detail=behind_detail,
                            status=SignalStatus.AVAILABLE,
                            evidence_ids=[ev_id],
                        )
                        signals.append(sig)
                        claims.append(cl)
                except InvalidVersion:
                    pass

    # 5. GitHub Repository Signals
    if github_data is not None:
        if github_data.status == SignalStatus.UNAVAILABLE:
            sig, cl = _make_signal_and_claim(
                name="GITHUB_METADATA",
                points=0,
                detail="GitHub repository metadata unavailable",
                status=SignalStatus.UNAVAILABLE,
                evidence_ids=[],
                unavailable_reason=github_data.unavailable_reason,
            )
            signals.append(sig)
            claims.append(cl)
        elif github_data.evidence:
            ev_id = github_data.evidence.id
            if github_data.is_archived:
                sig, cl = _make_signal_and_claim(
                    name="REPO_ARCHIVED",
                    points=POINTS_REPO_ARCHIVED,
                    detail="Upstream GitHub repository is archived (read-only)",
                    status=SignalStatus.AVAILABLE,
                    evidence_ids=[ev_id],
                )
                signals.append(sig)
                claims.append(cl)

    # 6. OSV Vulnerability Signals
    if osv_data is not None:
        if osv_data.status == SignalStatus.UNAVAILABLE:
            sig, cl = _make_signal_and_claim(
                name="KNOWN_VULNERABILITIES",
                points=0,
                detail="OSV vulnerability lookup unavailable",
                status=SignalStatus.UNAVAILABLE,
                evidence_ids=[],
                unavailable_reason=osv_data.unavailable_reason,
            )
            signals.append(sig)
            claims.append(cl)
        elif osv_data.evidence and osv_data.vuln_count > 0:
            sev_multiplier = 20 if osv_data.max_severity == "HIGH" else 10
            vuln_points = min(MAX_VULNERABILITY_POINTS, osv_data.vuln_count * sev_multiplier)
            vuln_summary = f"{osv_data.vuln_count} vulnerability advisory(ies) found on OSV.dev"
            if osv_data.max_severity:
                vuln_summary += f" (max severity: {osv_data.max_severity})"
            if osv_data.vuln_ids:
                vuln_summary += f" [{', '.join(osv_data.vuln_ids[:3])}]"

            sig, cl = _make_signal_and_claim(
                name="KNOWN_VULNERABILITIES",
                points=vuln_points,
                detail=vuln_summary,
                status=SignalStatus.AVAILABLE,
                evidence_ids=[osv_data.evidence.id],
            )
            signals.append(sig)
            claims.append(cl)

    total_score = sum(s.points for s in signals)

    return EvaluatedRisk(
        dependency_name=dep.name,
        risk_score=total_score,
        signals=signals,
        claims=claims,
        resolved_version=resolved_ver,
    )
