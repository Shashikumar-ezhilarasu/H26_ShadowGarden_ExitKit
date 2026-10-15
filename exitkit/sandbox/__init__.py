"""Isolated sandbox execution: runs builds, tests, and verifications in temporary worktrees."""

from __future__ import annotations

from exitkit.sandbox.differential import run_differential_suite
from exitkit.sandbox.executor import run_sandbox_verification
from exitkit.sandbox.migrator import apply_migration
from exitkit.sandbox.models import (
    CommandRunResult,
    DifferentialCaseResult,
    DifferentialSuiteResult,
    SandboxVerificationResult,
    TestRunResult,
)
from exitkit.sandbox.runner import SandboxRunner
from exitkit.sandbox.workspace import isolated_workspace

__all__ = [
    "CommandRunResult",
    "DifferentialCaseResult",
    "DifferentialSuiteResult",
    "SandboxRunner",
    "SandboxVerificationResult",
    "TestRunResult",
    "apply_migration",
    "isolated_workspace",
    "run_differential_suite",
    "run_sandbox_verification",
]
