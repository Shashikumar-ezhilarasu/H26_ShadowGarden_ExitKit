"""Prompt templates and contracts for the Gemma bounded reasoner."""

from __future__ import annotations

import json
from typing import Any

from exitkit.discover.models import DiscoveredDependency
from exitkit.scan.risk import EvaluatedRisk
from exitkit.usage.models import DependencyUsage

SYSTEM_PROMPT = """You are ExitKit's bounded dependency migration reasoner.
Your goal is to evaluate an at-risk Python dependency and formulate a migration plan.

Hard Rules:
1. Target Python versions are 3.12 and 3.13 only (requires-python = ">=3.12,<3.14").
2. Migration Strategy Priority:
   a. UPGRADE FIRST: Always evaluate upgrading within the same package to a modern version
      (e.g. Jinja2==2.11.3 -> Jinja2>=3.0.0). If an upgrade resolves compatibility, security,
      or syntax issues, you MUST choose UPGRADE.
   b. REPLACE SECOND: If the package is unmaintained, abandoned, or officially superseded by
      modern community standards (e.g., appdirs -> platformdirs, toml -> tomllib on 3.11+),
      choose REPLACE.
   c. REMOVE_OR_VENDOR LAST: Only if the usage is a trivial utility function that can be
      implemented cleanly in pure Python standard library without external dependencies.
3. Every step in migration_steps must be an explicit, concrete code or manifest modification.
4. Output MUST be valid JSON adhering strictly to the required schema."""

PROPOSAL_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "target_dependency": {"type": "string"},
        "proposed_package": {"type": "string"},
        "proposed_specifier": {"type": "string"},
        "kind": {
            "type": "string",
            "enum": ["UPGRADE", "REPLACE", "REMOVE_OR_VENDOR"],
        },
        "rationale": {"type": "string"},
        "migration_steps": {
            "type": "array",
            "items": {"type": "string"},
        },
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "expected_shim_needed": {"type": "boolean"},
        "shim_code": {"type": ["string", "null"]},
    },
    "required": [
        "target_dependency",
        "proposed_package",
        "proposed_specifier",
        "kind",
        "rationale",
        "migration_steps",
        "confidence",
        "expected_shim_needed",
    ],
    "additionalProperties": False,
}


def build_proposer_prompt(
    dep: DiscoveredDependency,
    risk: EvaluatedRisk | None = None,
    usage: DependencyUsage | None = None,
) -> str:
    """Build a detailed context prompt for Gemma 4."""
    declared = ", ".join(f"{s.file}:{s.specifier}" for s in dep.declared_specifiers) or "*"
    resolved = dep.resolved_version or "unknown"

    signals_text = "No risk scan signals recorded."
    if risk and risk.signals:
        sig_lines = [
            f"- [{s.points} pts] {s.name}: {s.detail}" for s in risk.signals if s.points > 0
        ]
        if sig_lines:
            signals_text = f"Risk Score: {risk.risk_score}/100\n" + "\n".join(sig_lines)

    usage_text = "No usage found in source code."
    if usage:
        files = ", ".join(usage.files_touched) or "None"
        symbols = ", ".join(usage.symbols_used) or "None"
        call_count = usage.blast_radius.total_call_sites
        blast_level = usage.blast_radius.critical_path_level.value
        snippets = "\n".join(
            f"  [{s.file}:{s.line}] ({s.kind.value}) {s.snippet}" for s in usage.sites[:10]
        )
        usage_text = (
            f"Files touched ({len(usage.files_touched)}): {files}\n"
            f"Blast Radius Level: {blast_level}\n"
            f"Total Call Sites: {call_count}\n"
            f"Symbols Used: {symbols}\n"
            f"Key Code Snippets:\n{snippets}"
        )

    prompt = f"""Evaluate dependency '{dep.name}' and generate a structured migration proposal.

Target Dependency Information:
- Package: {dep.name} (canonical: {dep.canonical_name})
- Declared in Manifests: {declared}
- Resolved Version in Target: {resolved}

Observed Risk Signals & Evidence:
{signals_text}

AST Code Usage & Blast Radius:
{usage_text}

Remember the priority:
1. UPGRADE first: Can we upgrade within '{dep.name}' to fix compatibility on Python 3.12/3.13?
2. REPLACE second: If '{dep.name}' is deprecated/unmaintained, what is the modern replacement?
3. REMOVE_OR_VENDOR third: Can it be eliminated?

Return a single JSON object strictly matching this schema:
{json.dumps(PROPOSAL_JSON_SCHEMA, indent=2)}
"""
    return prompt
