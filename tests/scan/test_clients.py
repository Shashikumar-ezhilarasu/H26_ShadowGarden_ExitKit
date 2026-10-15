"""Tests for HTTP clients and caching using recorded mock responses."""

from __future__ import annotations

from pathlib import Path

import httpx

from exitkit.ledger.models import SignalStatus
from exitkit.ledger.store import EvidenceStore
from exitkit.scan.cache import NetworkCache
from exitkit.scan.clients import EvidenceHttpClient


def create_mock_transport() -> httpx.MockTransport:
    """Create a mock transport responding to PyPI, GitHub, and OSV requests."""

    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)

        if "pypi.org/pypi/jinja2/json" in url_str:
            pypi_payload = {
                "info": {
                    "version": "3.1.4",
                    "yanked": False,
                    "classifiers": [
                        "Development Status :: 5 - Production/Stable",
                        "Programming Language :: Python :: 3.12",
                        "Programming Language :: Python :: 3.13",
                    ],
                    "project_urls": {"Source": "https://github.com/pallets/jinja"},
                },
                "releases": {
                    "2.11.3": [{"upload_time": "2021-01-31T15:00:00Z"}],
                    "3.1.4": [{"upload_time": "2024-05-05T12:00:00Z"}],
                },
            }
            return httpx.Response(200, json=pypi_payload)

        if "api.github.com/repos/pallets/jinja" in url_str:
            gh_payload = {
                "archived": False,
                "disabled": False,
                "open_issues_count": 12,
                "pushed_at": "2024-05-06T10:00:00Z",
            }
            return httpx.Response(200, json=gh_payload)

        if "api.osv.dev/v1/query" in url_str:
            osv_payload = {
                "vulns": [
                    {
                        "id": "GHSA-h5c8-rqrq-552m",
                        "summary": "HTML attribute injection vulnerability",
                        "database_specific": {"severity": "HIGH"},
                    }
                ]
            }
            return httpx.Response(200, json=osv_payload)

        if "pypi.org/pypi/nonexistent/json" in url_str:
            return httpx.Response(404, json={"message": "Not found"})

        return httpx.Response(500, text="Server error")

    return httpx.MockTransport(handler)


def test_evidence_http_client_pypi_github_osv(tmp_path: Path) -> None:
    """Verify mock client correctly queries PyPI, GitHub, and OSV endpoints."""
    store = EvidenceStore(tmp_path)
    transport = create_mock_transport()
    client = EvidenceHttpClient(
        evidence_store=store,
        use_cache=True,
        offline=False,
        transport=transport,
    )

    # 1. PyPI fetch
    pypi_data = client.fetch_pypi("jinja2", target_version="2.11.3")
    assert pypi_data.status == SignalStatus.AVAILABLE
    assert pypi_data.latest_version == "3.1.4"
    assert pypi_data.declares_py312
    assert pypi_data.declares_py313
    assert pypi_data.github_repo == ("pallets", "jinja")
    assert pypi_data.evidence is not None
    assert pypi_data.target_version_date is not None

    # 2. GitHub fetch
    gh_data = client.fetch_github("pallets", "jinja")
    assert gh_data.status == SignalStatus.AVAILABLE
    assert not gh_data.is_archived
    assert gh_data.open_issues_count == 12
    assert gh_data.evidence is not None

    # 3. OSV fetch
    osv_data = client.fetch_osv("jinja2", "2.11.3")
    assert osv_data.status == SignalStatus.AVAILABLE
    assert osv_data.vuln_count == 1
    assert osv_data.max_severity == "HIGH"
    assert osv_data.evidence is not None

    # 4. 404 handling
    missing_data = client.fetch_pypi("nonexistent")
    assert missing_data.status == SignalStatus.UNAVAILABLE
    assert "not found" in (missing_data.unavailable_reason or "").lower()


def test_network_cache_offline_behavior(tmp_path: Path) -> None:
    """Verify cache storage, hit retrieval, and offline miss handling."""
    cache = NetworkCache(cache_dir=tmp_path / "cache")
    test_url = "https://example.com/api/test"

    # Initially not in cache
    assert cache.get(test_url) is None

    # Populate cache
    sample_content = b'{"status": "ok"}'
    cache.set(test_url, 200, {"content-type": "application/json"}, sample_content)

    # Retrieval hit
    cached = cache.get(test_url)
    assert cached is not None
    assert cached.content == sample_content
    assert cached.status_code == 200

    # Offline mode with client
    store = EvidenceStore(tmp_path / "run")
    client_offline = EvidenceHttpClient(
        evidence_store=store,
        cache=cache,
        use_cache=True,
        offline=True,
    )

    # Uncached URL in offline mode must return UNAVAILABLE
    uncached = client_offline.fetch_pypi("uncached-package")
    assert uncached.status == SignalStatus.UNAVAILABLE
    assert "offline" in (uncached.unavailable_reason or "").lower()
