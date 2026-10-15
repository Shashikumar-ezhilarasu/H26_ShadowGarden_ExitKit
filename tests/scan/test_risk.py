"""Unit tests for the risk evaluation engine and signal scoring."""

from __future__ import annotations

from datetime import UTC, datetime

from exitkit.discover.models import (
    DeclaredSpecifierRecord,
    DependencyScope,
    DiscoveredDependency,
    ManifestDivergence,
)
from exitkit.ledger.models import Evidence, EvidenceKind, SignalStatus, TrustLevel
from exitkit.scan.clients import GitHubData, PypiData
from exitkit.scan.risk import (
    POINTS_IMPORT_FAILS_PY312,
    POINTS_INACTIVE_CLASSIFIER,
    POINTS_INCOMPATIBLE_DIVERGENCE,
    POINTS_REPO_ARCHIVED,
    POINTS_YANKED_VERSION,
    evaluate_dependency_risk,
)
from exitkit.scan.smoke import SmokeResult


def dummy_evidence(ev_id: str = "ev-risk-1") -> Evidence:
    return Evidence(
        id=ev_id,
        kind=EvidenceKind.HTTP_RESPONSE,
        path=f"{ev_id}.json",
        sha256="d" * 64,
        size_bytes=250,
        created_at=datetime.now(UTC),
        description="Dummy evidence payload",
    )


def test_risk_score_sum_points_and_signals() -> None:
    """Evaluate risk signals and verify total score equals sum of signal points."""
    ev = dummy_evidence("ev-test-1")
    dep = DiscoveredDependency(
        name="Jinja2",
        canonical_name="jinja2",
        declared_specifiers=[
            DeclaredSpecifierRecord(
                file="pyproject.toml",
                line=1,
                specifier="==2.11.3",
                scope=DependencyScope.RUNTIME,
            )
        ],
        divergence=ManifestDivergence(
            files=["pyproject.toml", "requirements.txt"],
            specifiers=[">=3.0", "==2.11.3"],
            compatible=False,
            detail="Incompatible",
        ),
    )

    smoke_fail = SmokeResult(
        status=SignalStatus.AVAILABLE,
        python_version="3.12",
        install_passed=True,
        import_passed=False,
        import_evidence=ev,
        error_summary="ImportError: cannot import name 'soft_unicode' from 'markupsafe'",
    )

    pypi_data = PypiData(
        status=SignalStatus.AVAILABLE,
        package_name="jinja2",
        is_inactive=True,
        is_yanked=True,
        evidence=ev,
    )

    gh_data = GitHubData(
        status=SignalStatus.AVAILABLE,
        is_archived=True,
        evidence=ev,
    )

    risk = evaluate_dependency_risk(
        dep=dep,
        pypi_data=pypi_data,
        github_data=gh_data,
        smoke_312=smoke_fail,
        smoke_313=None,
        osv_data=None,
    )

    expected_score = (
        POINTS_INCOMPATIBLE_DIVERGENCE
        + POINTS_IMPORT_FAILS_PY312
        + POINTS_YANKED_VERSION
        + POINTS_INACTIVE_CLASSIFIER
        + POINTS_REPO_ARCHIVED
        + 15  # NO_PY312_OR_PY313_SUPPORT
    )
    assert risk.risk_score == expected_score
    assert risk.risk_score == sum(s.points for s in risk.signals)

    # Every signal must have a matching claim
    assert len(risk.signals) == len(risk.claims)
    for sig, cl in zip(risk.signals, risk.claims, strict=False):
        assert sig.claim_id == cl.id
        assert cl.trust_level == TrustLevel.OBSERVED


def test_unavailable_signal_awards_zero_points_and_unverified_claim() -> None:
    """Unavailable external signals award 0 points and generate UNVERIFIED claims
    with explanation notes.
    """
    dep = DiscoveredDependency(name="dummy", canonical_name="dummy")

    pypi_unavail = PypiData(
        status=SignalStatus.UNAVAILABLE,
        package_name="dummy",
        unavailable_reason="PyPI API timed out",
    )

    smoke_unavail = SmokeResult(
        status=SignalStatus.UNAVAILABLE,
        python_version="3.12",
        unavailable_reason="Python 3.12 not found on host",
    )

    risk = evaluate_dependency_risk(
        dep=dep,
        pypi_data=pypi_unavail,
        github_data=None,
        smoke_312=smoke_unavail,
        smoke_313=None,
        osv_data=None,
    )

    # Risk score must be 0 for unavailable signals
    assert risk.risk_score == 0

    unverified_claims = [c for c in risk.claims if c.trust_level == TrustLevel.UNVERIFIED]
    assert len(unverified_claims) >= 2
    for c in unverified_claims:
        assert c.note is not None
        assert len(c.note) > 0


def test_non_pypi_dependency_handling() -> None:
    """Non-PyPI dependencies produce informational 0-point signal and UNVERIFIED claim."""
    dep = DiscoveredDependency(
        name="custom-git",
        canonical_name="custom-git",
        is_pypi=False,
        url_or_vcs="git+https://github.com/org/repo.git",
    )

    risk = evaluate_dependency_risk(dep, None, None, None, None, None)
    assert risk.risk_score == 0
    assert len(risk.signals) == 1
    assert risk.signals[0].name == "NON_PYPI_SOURCE"
    assert risk.claims[0].trust_level == TrustLevel.UNVERIFIED
