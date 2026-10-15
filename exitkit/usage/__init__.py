"""Usage mapping and blast radius analysis package."""

from __future__ import annotations

from exitkit.usage.analyzer import compute_blast_radius, map_repository_usage
from exitkit.usage.models import (
    BlastRadius,
    CriticalPathLevel,
    DependencyUsage,
    UsageKind,
    UsageReport,
    UsageSite,
)
from exitkit.usage.resolver import (
    is_stdlib_module,
    resolve_dist_to_modules,
    resolve_module_to_dist,
)

__all__ = [
    "BlastRadius",
    "CriticalPathLevel",
    "DependencyUsage",
    "UsageKind",
    "UsageReport",
    "UsageSite",
    "compute_blast_radius",
    "is_stdlib_module",
    "map_repository_usage",
    "resolve_dist_to_modules",
    "resolve_module_to_dist",
]
