"""HTTP API clients for PyPI, GitHub, and OSV evidence gathering."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from exitkit.ledger.models import Evidence, EvidenceKind, SignalStatus
from exitkit.ledger.store import EvidenceStore
from exitkit.scan.cache import NetworkCache


@dataclass(frozen=True)
class PypiData:
    """Parsed PyPI package metadata."""

    status: SignalStatus
    package_name: str
    latest_version: str | None = None
    latest_release_date: datetime | None = None
    target_version_date: datetime | None = None
    releases_last_24m: int = 0
    is_yanked: bool = False
    requires_python: str | None = None
    classifiers: list[str] | None = None
    is_inactive: bool = False
    declares_py312: bool = False
    declares_py313: bool = False
    project_urls: dict[str, str] | None = None
    github_repo: tuple[str, str] | None = None
    evidence: Evidence | None = None
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class GitHubData:
    """Parsed GitHub repository metadata."""

    status: SignalStatus
    is_archived: bool = False
    is_disabled: bool = False
    pushed_at: datetime | None = None
    open_issues_count: int = 0
    evidence: Evidence | None = None
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class OsvData:
    """Parsed OSV.dev vulnerability query results."""

    status: SignalStatus
    vuln_count: int = 0
    max_severity: str | None = None
    vuln_ids: list[str] | None = None
    evidence: Evidence | None = None
    unavailable_reason: str | None = None


GITHUB_URL_PATTERN = re.compile(
    r"https?://github\.com/([a-zA-Z0-9_\-\.]+)/([a-zA-Z0-9_\-\.]+)(?:/.*)?$"
)


class EvidenceHttpClient:
    """Fetches remote evidence from PyPI, GitHub, and OSV with caching and retry backoff."""

    def __init__(
        self,
        evidence_store: EvidenceStore,
        cache: NetworkCache | None = None,
        use_cache: bool = True,
        offline: bool = False,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.store = evidence_store
        self.cache = cache or NetworkCache()
        self.use_cache = use_cache
        self.offline = offline
        self.timeout = timeout
        self.transport = transport

    def _fetch_url(
        self,
        url: str,
        method: str = "GET",
        json_body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes, bool]:
        """Fetch URL with cache checking. Returns (status_code, headers, content, from_cache)."""
        clean_url = url.strip()
        hdrs = headers or {}

        # 1. Check cache
        if self.use_cache:
            cached = self.cache.get(clean_url, ignore_ttl=self.offline)
            if cached is not None:
                return cached.status_code, cached.headers, cached.content, True

        if self.offline:
            raise httpx.RequestError(f"Offline mode: cache miss for {clean_url}")

        # 2. Live HTTP request
        client_kwargs: dict[str, Any] = {"timeout": self.timeout}
        if self.transport:
            client_kwargs["transport"] = self.transport

        with httpx.Client(**client_kwargs) as client:
            resp = client.request(method, clean_url, json=json_body, headers=hdrs)
            resp_headers = dict(resp.headers.items())
            content = resp.content

            if self.use_cache and resp.status_code == 200:
                self.cache.set(clean_url, resp.status_code, resp_headers, content)

            return resp.status_code, resp_headers, content, False

    def fetch_pypi(self, package_name: str, target_version: str | None = None) -> PypiData:
        """Fetch package metadata from PyPI JSON API."""
        url = f"https://pypi.org/pypi/{package_name}/json"
        try:
            status_code, _, content, from_cache = self._fetch_url(url)
            if status_code == 404:
                return PypiData(
                    status=SignalStatus.UNAVAILABLE,
                    package_name=package_name,
                    unavailable_reason="Package not found on PyPI (404)",
                )
            if status_code != 200:
                return PypiData(
                    status=SignalStatus.UNAVAILABLE,
                    package_name=package_name,
                    unavailable_reason=f"PyPI returned HTTP status {status_code}",
                )

            desc = f"PyPI JSON payload for {package_name}" + (" (from cache)" if from_cache else "")
            ev = self.store.add_bytes(
                kind=EvidenceKind.HTTP_RESPONSE,
                content=content,
                description=desc,
                ext="json",
            )

            import json

            data = json.loads(content.decode("utf-8"))
            info = data.get("info", {})
            releases = data.get("releases", {})

            latest_ver = info.get("version")
            classifiers = info.get("classifiers", []) or []
            requires_py = info.get("requires_python")
            is_yanked = bool(info.get("yanked", False))

            is_inactive = any(
                "development status :: 7 - inactive" in c.lower() for c in classifiers
            )
            has_312 = any(
                "programming language :: python :: 3.12" in c.lower() for c in classifiers
            )
            has_313 = any(
                "programming language :: python :: 3.13" in c.lower() for c in classifiers
            )

            # Release timestamps
            now = datetime.now(UTC)
            two_years_ago = now - timedelta(days=730)
            releases_24m = 0
            latest_date: datetime | None = None
            target_date: datetime | None = None

            for ver_str, file_list in releases.items():
                if not file_list:
                    continue
                # Pick first upload time
                upload_str = file_list[0].get("upload_time_iso_8601") or file_list[0].get(
                    "upload_time"
                )
                if upload_str:
                    try:
                        u_dt = datetime.fromisoformat(upload_str.replace("Z", "+00:00"))
                        if u_dt.tzinfo is None:
                            u_dt = u_dt.replace(tzinfo=UTC)
                        if u_dt >= two_years_ago:
                            releases_24m += 1
                        if ver_str == latest_ver:
                            latest_date = u_dt
                        if target_version and ver_str == target_version:
                            target_date = u_dt
                    except Exception:
                        pass

            # GitHub repo extraction from project_urls or home_page
            project_urls = dict(info.get("project_urls") or {})
            if info.get("home_page"):
                project_urls["Homepage"] = info["home_page"]

            gh_repo: tuple[str, str] | None = None
            for p_url in project_urls.values():
                if isinstance(p_url, str):
                    m = GITHUB_URL_PATTERN.match(p_url.strip().rstrip("/"))
                    if m:
                        owner = m.group(1)
                        repo = m.group(2).removesuffix(".git")
                        gh_repo = (owner, repo)
                        break

            return PypiData(
                status=SignalStatus.AVAILABLE,
                package_name=package_name,
                latest_version=latest_ver,
                latest_release_date=latest_date,
                target_version_date=target_date,
                releases_last_24m=releases_24m,
                is_yanked=is_yanked,
                requires_python=requires_py,
                classifiers=classifiers,
                is_inactive=is_inactive,
                declares_py312=has_312,
                declares_py313=has_313,
                project_urls=project_urls,
                github_repo=gh_repo,
                evidence=ev,
            )

        except Exception as exc:
            return PypiData(
                status=SignalStatus.UNAVAILABLE,
                package_name=package_name,
                unavailable_reason=f"Failed to fetch PyPI metadata: {exc}",
            )

    def fetch_github(self, owner: str, repo: str) -> GitHubData:
        """Fetch repository metadata from GitHub REST API."""
        url = f"https://api.github.com/repos/{owner}/{repo}"
        headers = {"Accept": "application/vnd.github.v3+json"}
        token = os.getenv("GITHUB_TOKEN")
        if token and token.strip():
            headers["Authorization"] = f"Bearer {token.strip()}"

        try:
            status_code, _, content, from_cache = self._fetch_url(url, headers=headers)
            if status_code in (403, 429):
                return GitHubData(
                    status=SignalStatus.UNAVAILABLE,
                    unavailable_reason=f"GitHub API rate limit exceeded ({status_code})",
                )
            if status_code == 404:
                return GitHubData(
                    status=SignalStatus.UNAVAILABLE,
                    unavailable_reason="GitHub repository not found (404)",
                )
            if status_code != 200:
                return GitHubData(
                    status=SignalStatus.UNAVAILABLE,
                    unavailable_reason=f"GitHub API returned HTTP status {status_code}",
                )

            desc = f"GitHub repo metadata for {owner}/{repo}" + (
                " (from cache)" if from_cache else ""
            )
            ev = self.store.add_bytes(
                kind=EvidenceKind.HTTP_RESPONSE,
                content=content,
                description=desc,
                ext="json",
            )

            import json

            data = json.loads(content.decode("utf-8"))
            is_archived = bool(data.get("archived", False))
            is_disabled = bool(data.get("disabled", False))
            open_issues = int(data.get("open_issues_count", 0))

            pushed_dt: datetime | None = None
            if data.get("pushed_at"):
                try:
                    pushed_dt = datetime.fromisoformat(data["pushed_at"].replace("Z", "+00:00"))
                    if pushed_dt.tzinfo is None:
                        pushed_dt = pushed_dt.replace(tzinfo=UTC)
                except Exception:
                    pass

            return GitHubData(
                status=SignalStatus.AVAILABLE,
                is_archived=is_archived,
                is_disabled=is_disabled,
                pushed_at=pushed_dt,
                open_issues_count=open_issues,
                evidence=ev,
            )

        except Exception as exc:
            return GitHubData(
                status=SignalStatus.UNAVAILABLE,
                unavailable_reason=f"Failed to fetch GitHub metadata: {exc}",
            )

    def fetch_osv(self, package_name: str, version: str | None = None) -> OsvData:
        """Query OSV.dev for known vulnerabilities."""
        if not version:
            return OsvData(
                status=SignalStatus.UNAVAILABLE,
                unavailable_reason="No resolved version to query OSV vulnerabilities",
            )

        url = "https://api.osv.dev/v1/query"
        body = {"package": {"name": package_name, "ecosystem": "PyPI"}, "version": version}

        try:
            status_code, _, content, from_cache = self._fetch_url(
                url, method="POST", json_body=body, headers={"Content-Type": "application/json"}
            )
            if status_code != 200:
                return OsvData(
                    status=SignalStatus.UNAVAILABLE,
                    unavailable_reason=f"OSV.dev returned HTTP status {status_code}",
                )

            desc = f"OSV.dev vulnerabilities for {package_name}=={version}" + (
                " (from cache)" if from_cache else ""
            )
            ev = self.store.add_bytes(
                kind=EvidenceKind.HTTP_RESPONSE,
                content=content,
                description=desc,
                ext="json",
            )

            import json

            data = json.loads(content.decode("utf-8"))
            vulns = data.get("vulns", []) or []
            vuln_ids = [v.get("id") for v in vulns if v.get("id")]

            # Determine max severity
            severities: list[str] = []
            for v in vulns:
                for s in v.get("severity", []):
                    score = s.get("score")
                    if isinstance(score, str) and score.startswith("CVSS"):
                        # Extract CVSS base score if present
                        severities.append("CVSS")
                if "database_specific" in v and "severity" in v["database_specific"]:
                    severities.append(str(v["database_specific"]["severity"]).upper())

            max_sev = (
                "HIGH"
                if any("HIGH" in s or "CRITICAL" in s for s in severities)
                else (
                    "MEDIUM"
                    if any("MODERATE" in s or "MEDIUM" in s for s in severities)
                    else ("LOW" if severities else None)
                )
            )

            return OsvData(
                status=SignalStatus.AVAILABLE,
                vuln_count=len(vulns),
                max_severity=max_sev,
                vuln_ids=vuln_ids,
                evidence=ev,
            )

        except Exception as exc:
            return OsvData(
                status=SignalStatus.UNAVAILABLE,
                unavailable_reason=f"Failed to query OSV.dev: {exc}",
            )
