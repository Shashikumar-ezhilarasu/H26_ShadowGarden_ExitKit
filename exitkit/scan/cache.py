"""HTTP and repository caching for evidence gathering."""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class CachedResponse:
    """A cached HTTP response payload with metadata."""

    url: str
    status_code: int
    headers: dict[str, str]
    content: bytes
    timestamp: datetime

    @property
    def is_expired(self) -> bool:
        """Check if cached entry is older than 24 hours."""
        age_seconds = (datetime.now(UTC) - self.timestamp).total_seconds()
        return age_seconds > 86400


class NetworkCache:
    """Filesystem cache for HTTP responses with a 24-hour TTL."""

    def __init__(self, cache_dir: Path | str = Path(".exitkit/cache/http")) -> None:
        self.cache_dir = Path(cache_dir).resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _url_key(self, url: str) -> str:
        return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()

    def get(self, url: str, ignore_ttl: bool = False) -> CachedResponse | None:
        """Retrieve a cached response if present and valid."""
        key = self._url_key(url)
        entry_file = self.cache_dir / f"{key}.json"
        if not entry_file.exists():
            return None

        try:
            data = json.loads(entry_file.read_text(encoding="utf-8"))
            ts = datetime.fromisoformat(data["timestamp"])
            resp = CachedResponse(
                url=data["url"],
                status_code=data["status_code"],
                headers=data.get("headers", {}),
                content=base64.b64decode(data["content_b64"]),
                timestamp=ts,
            )
            if resp.is_expired and not ignore_ttl:
                return None
            return resp
        except Exception:
            return None

    def set(self, url: str, status_code: int, headers: dict[str, str], content: bytes) -> None:
        """Save an HTTP response into the cache."""
        key = self._url_key(url)
        entry_file = self.cache_dir / f"{key}.json"
        data = {
            "url": url,
            "status_code": status_code,
            "headers": {
                k: v for k, v in headers.items() if isinstance(k, str) and isinstance(v, str)
            },
            "content_b64": base64.b64encode(content).decode("ascii"),
            "timestamp": datetime.now(UTC).isoformat(),
        }
        entry_file.write_text(json.dumps(data, indent=2), encoding="utf-8")


def resolve_target_repo(repo_path_or_url: str) -> tuple[Path, bool]:
    """Resolve a target local repository path or shallow-clone a remote git URL.

    Returns (resolved_path, is_remote).
    """
    clean = repo_path_or_url.strip()
    is_remote = any(clean.startswith(proto) for proto in ("https://", "http://", "git@", "ssh://"))

    if is_remote:
        cache_base = Path(".exitkit/cache/repos").resolve()
        cache_base.mkdir(parents=True, exist_ok=True)
        url_hash = hashlib.sha256(clean.encode("utf-8")).hexdigest()[:16]
        repo_dir = cache_base / url_hash

        if not repo_dir.exists():
            cmd = ["git", "clone", "--depth", "1", clean, str(repo_dir)]
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=120)
        return repo_dir, True

    local_path = Path(clean).resolve()
    if not local_path.exists():
        raise FileNotFoundError(f"Target repository path does not exist: {local_path}")
    return local_path, False
