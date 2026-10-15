"""Data models for dependency discovery and manifest parsing."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class DependencyScope(StrEnum):
    """Scope where a dependency is declared."""

    RUNTIME = "runtime"
    DEV = "dev"
    TEST = "test"
    OPTIONAL = "optional"
    BUILD = "build"


class DeclaredSpecifierRecord(BaseModel):
    """A single declaration of a dependency in a manifest file."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    file: str
    line: int
    specifier: str
    scope: DependencyScope = DependencyScope.RUNTIME


class ManifestDivergence(BaseModel):
    """Details when a dependency is declared differently across manifests."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    files: list[str]
    specifiers: list[str]
    compatible: bool
    detail: str


class DiscoveredDependency(BaseModel):
    """A direct dependency discovered in a repository's manifests."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    canonical_name: str
    is_pypi: bool = True
    url_or_vcs: str | None = None
    declared_specifiers: list[DeclaredSpecifierRecord] = Field(default_factory=list)
    resolved_version: str | None = None
    is_commented_out: bool = False
    divergence: ManifestDivergence | None = None

    @property
    def primary_specifier(self) -> str:
        """Return the most specific or first declared specifier."""
        if not self.declared_specifiers:
            return ""
        return self.declared_specifiers[0].specifier

    @property
    def primary_scope(self) -> DependencyScope:
        """Return the primary scope (runtime takes precedence)."""
        scopes = {d.scope for d in self.declared_specifiers}
        if DependencyScope.RUNTIME in scopes:
            return DependencyScope.RUNTIME
        if DependencyScope.TEST in scopes:
            return DependencyScope.TEST
        if DependencyScope.DEV in scopes:
            return DependencyScope.DEV
        if DependencyScope.BUILD in scopes:
            return DependencyScope.BUILD
        return DependencyScope.OPTIONAL


class DiscoveryResult(BaseModel):
    """Aggregate result of dependency discovery on a target repository."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    target_path: str
    dependencies: list[DiscoveredDependency] = Field(default_factory=list)
    primary_manifest: str | None = None
    commented_out_pins: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
