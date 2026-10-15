"""Dependency discovery package."""

from exitkit.discover.manifests import discover_dependencies
from exitkit.discover.models import (
    DeclaredSpecifierRecord,
    DependencyScope,
    DiscoveredDependency,
    DiscoveryResult,
    ManifestDivergence,
)

__all__ = [
    "DeclaredSpecifierRecord",
    "DependencyScope",
    "DiscoveredDependency",
    "DiscoveryResult",
    "ManifestDivergence",
    "discover_dependencies",
]
