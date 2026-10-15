"""ExitKit Comparator module: compatibility gaps, honest limitations, and exit readiness."""

from exitkit.compare.analyzer import (
    extract_compatibility_gaps,
    extract_limitations,
    run_comparison,
)
from exitkit.compare.models import (
    AnalysisLimitation,
    ComparisonReport,
    CompatibilityGap,
    GapCategory,
    GapSeverity,
    GapTriage,
    LimitationCategory,
    ReadinessBreakdown,
    ReadinessTier,
)
from exitkit.compare.scoring import compute_exit_readiness_score

__all__ = [
    "AnalysisLimitation",
    "ComparisonReport",
    "CompatibilityGap",
    "GapCategory",
    "GapSeverity",
    "GapTriage",
    "LimitationCategory",
    "ReadinessBreakdown",
    "ReadinessTier",
    "compute_exit_readiness_score",
    "extract_compatibility_gaps",
    "extract_limitations",
    "run_comparison",
]
