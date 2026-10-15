"""Data models for AST usage mapping and blast radius analysis."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class UsageKind(StrEnum):
    """Category of dependency usage in source code."""

    IMPORT = "IMPORT"
    CALL = "CALL"
    ATTRIBUTE_ACCESS = "ATTRIBUTE_ACCESS"
    TYPE_ANNOTATION = "TYPE_ANNOTATION"


class CriticalPathLevel(StrEnum):
    """Qualitative severity rating of a dependency's blast radius."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class UsageSite(BaseModel):
    """An exact location in source code where a dependency symbol is used."""

    file: str = Field(description="Relative path of the source file from repo root")
    line: int = Field(description="1-indexed line number")
    col: int = Field(description="0-indexed column offset")
    symbol: str = Field(description="Qualified symbol name, e.g. appdirs.user_cache_dir")
    kind: UsageKind = Field(description="Type of usage")
    snippet: str = Field(description="Single-line code snippet showing the usage")

    model_config = {"frozen": True}


class BlastRadius(BaseModel):
    """Summary of the blast radius and architectural risk of a dependency."""

    files_touched_count: int = Field(
        ge=0,
        description="Number of source files referencing the dependency",
    )
    total_source_files: int = Field(
        ge=0,
        description="Total number of Python source files analyzed",
    )
    files_touched_share: float = Field(
        ge=0.0,
        le=1.0,
        description="Fraction of repository files touched",
    )
    total_call_sites: int = Field(
        ge=0,
        description="Total number of invocation sites",
    )
    critical_path_level: CriticalPathLevel = Field(
        description="Critical-path severity heuristic",
    )
    critical_path_reasons: list[str] = Field(
        default_factory=list,
        description="Explanations for the assigned level",
    )

    model_config = {"frozen": True}


class DependencyUsage(BaseModel):
    """Aggregated usage analysis for a single distribution package."""

    distribution_name: str = Field(description="Normalized distribution name (PEP 503)")
    top_level_modules: list[str] = Field(
        description="Importable module names corresponding to this package",
    )
    files_touched: list[str] = Field(description="Unique list of source files containing usages")
    symbols_used: list[str] = Field(description="Unique list of qualified symbols used")
    sites: list[UsageSite] = Field(description="All detected usage sites across the codebase")
    blast_radius: BlastRadius = Field(description="Computed blast radius for this dependency")

    model_config = {"frozen": True}


class UsageReport(BaseModel):
    """Full repository usage mapping and blast radius report."""

    target_path: str = Field(description="Path to target repository analyzed")
    total_source_files: int = Field(ge=0, description="Count of Python source files scanned")
    source_files: list[str] = Field(
        description="List of relative paths to all scanned source files",
    )
    usages: dict[str, DependencyUsage] = Field(
        default_factory=dict,
        description="Map from canonical distribution name to its detailed usage",
    )

    model_config = {"frozen": True}
