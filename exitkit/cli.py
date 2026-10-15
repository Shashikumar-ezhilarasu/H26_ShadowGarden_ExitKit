"""Command-line interface for ExitKit."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Annotated, Literal, cast

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from exitkit import __version__
from exitkit.compare import ReadinessTier, run_comparison
from exitkit.config import check_env_status
from exitkit.ledger.models import Capsule, ProposalKind
from exitkit.llm.client import ModelError
from exitkit.llm.proposer import run_propose
from exitkit.sandbox import run_sandbox_verification
from exitkit.scan.scanner import run_scan
from exitkit.usage.analyzer import map_repository_usage
from exitkit.usage.models import CriticalPathLevel, UsageKind

app = typer.Typer(
    name="exitkit",
    help="ExitKit: Don't trust the AI's migration plan. Trust the receipt.",
    no_args_is_help=True,
)
console = Console()


def version_callback(value: bool) -> None:
    """Print the version and exit."""
    if value:
        msg = f"[bold cyan]ExitKit[/bold cyan] version [bold green]{__version__}[/bold green]"
        console.print(msg)
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            "-v",
            help="Show the ExitKit version and exit.",
            callback=version_callback,
            is_eager=True,
        ),
    ] = None,
) -> None:
    """ExitKit CLI: Dynamic dependency resilience with cryptographically verified receipts."""


@app.command()
def doctor() -> None:
    """Check the local environment for required tools, Python version, and API keys."""
    env_status = check_env_status()

    python_version = sys.version.split()[0]
    uv_path = shutil.which("uv")
    git_path = shutil.which("git")

    table = Table(
        title="ExitKit Doctor System Report",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Component", style="dim", width=26)
    table.add_column("Status", width=16)
    table.add_column("Details")

    # Python Version Check
    py_ok = sys.version_info >= (3, 12) and sys.version_info < (3, 14)
    py_status = "[bold green]PASS[/bold green]" if py_ok else "[bold red]FAIL[/bold red]"
    py_detail = f"{python_version} ({sys.executable})"
    if not py_ok:
        py_detail += " - Requires Python >=3.12 and <3.14"
    table.add_row("Python Version", py_status, py_detail)

    # uv binary check
    uv_status = "[bold green]PRESENT[/bold green]" if uv_path else "[bold red]MISSING[/bold red]"
    uv_detail = uv_path if uv_path else "uv binary not found in PATH"
    table.add_row("uv Package Manager", uv_status, uv_detail)

    # git binary check
    git_status = "[bold green]PRESENT[/bold green]" if git_path else "[bold red]MISSING[/bold red]"
    git_detail = git_path if git_path else "git binary not found in PATH"
    table.add_row("Git Binary", git_status, git_detail)

    # GenAI / Gemini Key check (never reveal secret)
    genai_set = env_status.has_genai_key
    genai_status = (
        "[bold green]SET[/bold green]" if genai_set else "[bold yellow]NOT SET[/bold yellow]"
    )
    genai_details = []
    if env_status.google_api_key_set:
        genai_details.append("GOOGLE_API_KEY detected")
    if env_status.gemini_api_key_set:
        genai_details.append("GEMINI_API_KEY detected")
    if not genai_details:
        genai_details.append("Neither GOOGLE_API_KEY nor GEMINI_API_KEY is set")
    table.add_row("Google GenAI / Gemini Key", genai_status, ", ".join(genai_details))

    # Gemma Model check
    model_set = env_status.gemma_model_set
    model_status = (
        "[bold green]SET[/bold green]" if model_set else "[bold yellow]NOT SET[/bold yellow]"
    )
    model_detail = (
        f"Model: {env_status.gemma_model_name} (Backend: {env_status.llm_backend})"
        if model_set
        else "EXITKIT_GEMMA_MODEL is not set"
    )
    table.add_row("Gemma 4 Model ID", model_status, model_detail)

    # GitHub Token check (Optional)
    gh_status = (
        "[bold green]SET (optional)[/bold green]"
        if env_status.github_token_set
        else "[dim]NOT SET (optional)[/dim]"
    )
    gh_detail = "Configured" if env_status.github_token_set else "Optional for rate limit elevation"
    table.add_row("GitHub Token", gh_status, gh_detail)

    console.print(table)

    all_critical_passed = py_ok and bool(uv_path) and bool(git_path)
    if all_critical_passed:
        success_msg = (
            "[bold green]System check complete.[/bold green] "
            "Core environment dependencies are satisfied."
        )
        console.print(Panel(success_msg, title="Status", border_style="green"))
    else:
        fail_msg = (
            "[bold red]System check failed.[/bold red] "
            "Please resolve missing environment dependencies above."
        )
        console.print(Panel(fail_msg, title="Status", border_style="red"))

    if not model_set:
        console.print(
            "\n[dim]Gemma Proposer Setup:[/dim]\n"
            "  • Gemini API: export EXITKIT_GEMMA_MODEL=gemma-4-it (and GOOGLE_API_KEY)\n"
            "  • Ollama:     export EXITKIT_GEMMA_MODEL=gemma:7b (--backend ollama)\n"
            "  • Offline:    pass '--backend mock'"
        )


@app.command()
def schema(
    out: Annotated[
        Path | None,
        typer.Option(
            "--out",
            "-o",
            help="Path to write the Capsule JSON schema to.",
        ),
    ] = None,
) -> None:
    """Print or export the Dependency Exit Capsule JSON Schema."""
    schema_dict = Capsule.model_json_schema()
    formatted = json.dumps(schema_dict, indent=2)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(formatted + "\n", encoding="utf-8")
        console.print(f"[bold green]Capsule JSON schema written to:[/bold green] {out}")
    else:
        typer.echo(formatted)


@app.command()
def scan(
    repo_path_or_url: Annotated[
        str,
        typer.Argument(help="Path to local Python repository or remote git URL."),
    ],
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Output full scan result as JSON."),
    ] = False,
    out: Annotated[
        Path | None,
        typer.Option("--out", "-o", help="Custom output directory for run evidence and scan.json."),
    ] = None,
    only: Annotated[
        list[str] | None,
        typer.Option("--only", help="Filter scan to specific dependency names."),
    ] = None,
    offline: Annotated[
        bool,
        typer.Option("--offline", help="Run without network access; rely strictly on cache."),
    ] = False,
    no_cache: Annotated[
        bool,
        typer.Option("--no-cache", help="Bypass local HTTP cache and query live registries."),
    ] = False,
    timeout: Annotated[
        float,
        typer.Option("--timeout", help="Timeout in seconds for external commands and requests."),
    ] = 180.0,
    concurrency: Annotated[
        int,
        typer.Option("--concurrency", "-c", help="Concurrent worker count for scanning."),
    ] = 8,
) -> None:
    """Scan target Python repo for at-risk dependencies with cryptographically pinned receipts."""
    try:
        result = run_scan(
            repo_path_or_url=repo_path_or_url,
            only=only,
            use_cache=not no_cache,
            offline=offline,
            timeout=timeout,
            concurrency=concurrency,
            out_dir=out,
        )
    except Exception as exc:
        console.print(f"[bold red]Scan error:[/bold red] {exc}")
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(result.to_dict(), indent=2))
        return

    # Rich Table output
    table = Table(
        title=f"ExitKit Dependency Risk Scan: {result.target_path}",
        show_header=True,
        header_style="bold cyan",
        expand=True,
    )
    table.add_column("Dependency", style="bold", min_width=12, ratio=2)
    table.add_column("Scope", style="dim", width=7)
    table.add_column("Declared", width=11)
    table.add_column("Resolved", width=10)
    table.add_column("Py3.12", justify="center", width=7)
    table.add_column("Py3.13", justify="center", width=7)
    table.add_column("Score", justify="right", width=6)
    table.add_column("Top Signals", ratio=3)

    for s in result.scanned_dependencies:
        dep = s.dependency
        risk = s.risk

        # Format imports
        def fmt_status(st: bool | None) -> str:
            if st is True:
                return "[green]PASS[/green]"
            if st is False:
                return "[bold red]FAIL[/bold red]"
            return "[dim]N/A[/dim]"

        py312_str = fmt_status(s.py312_import_ok)
        py313_str = fmt_status(s.py313_import_ok)

        # Format score
        if risk.risk_score >= 40:
            score_str = f"[bold red]{risk.risk_score}[/bold red]"
        elif risk.risk_score > 0:
            score_str = f"[bold yellow]{risk.risk_score}[/bold yellow]"
        else:
            score_str = "[green]0[/green]"

        # Top signals
        top_sigs = [sig.name for sig in risk.signals if sig.points > 0]
        sig_str = ", ".join(top_sigs[:3]) if top_sigs else "[dim]None (Low Risk)[/dim]"

        table.add_row(
            dep.name,
            dep.primary_scope.value,
            dep.primary_specifier or "*",
            risk.resolved_version or "-",
            py312_str,
            py313_str,
            score_str,
            sig_str,
        )

    console.print(table)

    # Manifest Divergences Section
    if result.divergences:
        div_table = Table(
            title="Manifest Divergences Detected",
            show_header=True,
            header_style="bold yellow",
        )
        div_table.add_column("Files", style="dim")
        div_table.add_column("Declared Specifiers")
        div_table.add_column("Compatible?")
        div_table.add_column("Details")

        for d in result.divergences:
            compat_str = (
                "[green]YES[/green]" if d.compatible else "[bold red]NO (Incompatible)[/bold red]"
            )
            div_table.add_row(
                ", ".join(d.files),
                ", ".join(d.specifiers),
                compat_str,
                d.detail,
            )
        console.print(div_table)

    # Commented-out pins section
    if result.commented_out_pins:
        console.print(
            Panel(
                "\n".join(f"• {pin}" for pin in result.commented_out_pins),
                title="Commented-out Dependency Pins Detected",
                border_style="yellow",
            )
        )

    # Summary Panel
    verification_status = (
        "[bold green]ALL EVIDENCE CRYPTOGRAPHICALLY VERIFIED[/bold green]"
        if not result.verification_problems
        else f"[bold red]INTEGRITY PROBLEMS: {len(result.verification_problems)}[/bold red]"
    )

    summary_text = (
        f"Target: [bold]{result.target_path}[/bold]\n"
        f"Tree Fingerprint: [dim]{result.tree_fingerprint}[/dim]\n"
        f"Run Directory: [cyan]{result.run_dir}[/cyan]\n"
        f"Evidence Collected: [bold]{result.evidence_count}[/bold] artifacts | "
        f"Claims Tracked: [bold]{result.claims_count}[/bold]\n"
        f"Trust Ledger Status: {verification_status}"
    )
    console.print(Panel(summary_text, title="Scan Summary Receipt", border_style="cyan"))
    console.print(
        f"[bold green]target unchanged[/bold green] (fingerprint {result.tree_fingerprint[:16]}...)"
    )


@app.command("usage")
def usage_cmd(
    repo_path: Annotated[
        str,
        typer.Argument(help="Path to local target Python repository to map."),
    ],
    dep: Annotated[
        str | None,
        typer.Option("--dep", "-d", help="Filter analysis to a specific dependency name."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output full usage report as canonical JSON."),
    ] = False,
) -> None:
    """Map AST dependency usage, API surface, and blast radius across target repository."""
    try:
        report = map_repository_usage(repo_path, target_dep=dep)
    except Exception as exc:
        console.print(f"[bold red]Error running usage analysis:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc

    if json_output:
        console.print(json.dumps(report.model_dump(mode="json"), indent=2))
        return

    if not report.usages:
        console.print(f"[yellow]No dependency usage found in {report.target_path}[/yellow]")
        return

    # Blast Radius Summary Table
    table = Table(
        title=f"ExitKit Dependency Usage & Blast Radius: {report.target_path}",
        show_header=True,
        header_style="bold cyan",
        expand=True,
    )
    table.add_column("Dependency", style="bold", min_width=12)
    table.add_column("Files", justify="right", width=6)
    table.add_column("Share", justify="right", width=7)
    table.add_column("Calls", justify="right", width=6)
    table.add_column("Critical Path", justify="center", width=14)
    table.add_column("API Surface (Symbols Used)", ratio=1)

    level_colors = {
        CriticalPathLevel.CRITICAL: "[bold red]CRITICAL[/bold red]",
        CriticalPathLevel.HIGH: "[red]HIGH[/red]",
        CriticalPathLevel.MEDIUM: "[yellow]MEDIUM[/yellow]",
        CriticalPathLevel.LOW: "[green]LOW[/green]",
    }

    for dist_name, u in report.usages.items():
        blast = u.blast_radius
        lvl_str = level_colors.get(blast.critical_path_level, str(blast.critical_path_level.value))
        sym_str = (
            ", ".join(u.symbols_used[:5]) if u.symbols_used else "[dim]No active symbols[/dim]"
        )
        if len(u.symbols_used) > 5:
            sym_str += f" (+{len(u.symbols_used) - 5} more)"

        table.add_row(
            dist_name,
            str(blast.files_touched_count),
            f"{blast.files_touched_share:.1%}",
            str(blast.total_call_sites),
            lvl_str,
            sym_str,
        )

    console.print(table)

    # Detailed Usage Sites Table
    if dep:
        filter_key = dep.lower().replace("-", "_")
        matching = [
            u
            for k, u in report.usages.items()
            if k == dep.lower() or k.replace("-", "_") == filter_key
        ]
        if not matching:
            console.print(f"[yellow]No usage sites found for dependency '{dep}'.[/yellow]")
            return

        for u in matching:
            if not u.sites:
                console.print(f"[dim]No usage sites detected for {u.distribution_name}.[/dim]")
                continue

            site_table = Table(
                title=(
                    f"Code Usage Sites: {u.distribution_name} "
                    f"({len(u.sites)} sites across {len(u.files_touched)} files)"
                ),
                show_header=True,
                header_style="bold magenta",
                expand=True,
            )
            site_table.add_column("Kind", width=8)
            site_table.add_column("Location", style="dim", width=22)
            site_table.add_column("Symbol", style="bold", min_width=18, ratio=2)
            site_table.add_column("Code Snippet", ratio=3)

            kind_colors = {
                UsageKind.IMPORT: "[blue]IMPORT[/blue]",
                UsageKind.CALL: "[bold green]CALL[/bold green]",
                UsageKind.ATTRIBUTE_ACCESS: "[yellow]ATTR[/yellow]",
                UsageKind.TYPE_ANNOTATION: "[magenta]TYPE[/magenta]",
            }

            for site in u.sites:
                k_str = kind_colors.get(site.kind, site.kind.value)
                loc_str = f"{site.file}:{site.line}"
                site_table.add_row(
                    k_str,
                    loc_str,
                    site.symbol,
                    f"[dim]{site.snippet}[/dim]",
                )
            console.print(site_table)
    else:
        console.print(
            "[dim]Tip: Use 'exitkit usage <repo> --dep <name>' to inspect exact code snippets "
            "and call sites.[/dim]"
        )


@app.command("propose")
def propose_cmd(
    repo_path_or_url: Annotated[
        str,
        typer.Argument(help="Path to local Python repository or remote git URL."),
    ],
    dep: Annotated[
        str,
        typer.Option("--dep", "-d", help="Target dependency name to propose migration for."),
    ],
    model: Annotated[
        str | None,
        typer.Option("--model", "-m", help="Model ID override (defaults to EXITKIT_GEMMA_MODEL)."),
    ] = None,
    backend: Annotated[
        str | None,
        typer.Option("--backend", "-b", help="LLM backend: google | ollama | mock."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Output proposal and receipt claim as JSON."),
    ] = False,
    out: Annotated[
        Path | None,
        typer.Option("--out", "-o", help="Custom output directory for run evidence and proposal."),
    ] = None,
) -> None:
    """Generate an evidence-backed migration proposal for an at-risk dependency using Gemma 4."""
    valid_backends = ("google", "ollama", "mock")
    if backend and backend not in valid_backends:
        backends_str = ", ".join(valid_backends)
        console.print(
            f"[bold red]Invalid backend:[/bold red] '{backend}'. Supported backends: {backends_str}"
        )
        raise typer.Exit(1)

    try:
        b_lit = cast(Literal["google", "ollama", "mock"] | None, backend)
        res = run_propose(
            repo_path=repo_path_or_url,
            dep_name=dep,
            model_id=model,
            backend=b_lit,
            run_dir=out,
        )
    except ModelError as exc:
        console.print(f"[bold red]Gemma Configuration Error:[/bold red] {exc}")
        console.print(
            "\n[bold yellow]How to set up ExitKit Gemma Proposer:[/bold yellow]\n"
            "  1. Export model ID: export EXITKIT_GEMMA_MODEL=gemma-4-it\n"
            "  2. Export API key:  export GOOGLE_API_KEY=your_gemini_api_key\n"
            "  Or for local Ollama: export EXITKIT_GEMMA_MODEL=gemma:7b and pass --backend ollama\n"
            "  Or for mock testing: pass --backend mock"
        )
        raise typer.Exit(1) from exc
    except Exception as exc:
        console.print(f"[bold red]Proposer Error:[/bold red] {exc}")
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(res.model_dump(mode="json"), indent=2))
        return

    # Rich formatted output
    kind_colors = {
        ProposalKind.UPGRADE: "bold green",
        ProposalKind.REPLACE: "bold yellow",
        ProposalKind.REMOVE_OR_VENDOR: "bold red",
    }
    k_style = kind_colors.get(res.proposal.kind, "bold cyan")

    # Proposal Panel
    target_str = f"{res.proposal.proposed_package} {res.proposal.proposed_specifier}"
    shim_str = "Yes" if res.proposal.expected_shim_needed else "No"
    summary_text = (
        f"[bold]Target Dependency:[/bold] {res.target_dependency}\n"
        f"[bold]Strategy Kind:[/bold]     [{k_style}]{res.proposal.kind.value}[/{k_style}]\n"
        f"[bold]Proposed Target:[/bold]   [bold cyan]{target_str}[/bold cyan]\n"
        f"[bold]Confidence:[/bold]        {res.proposal.confidence * 100:.0f}%\n"
        f"[bold]Shim Required:[/bold]     {shim_str}\n\n"
        f"[bold]Architectural Rationale:[/bold]\n{res.proposal.rationale}"
    )
    console.print(
        Panel(
            summary_text,
            title=f"ExitKit Migration Proposal: {res.target_dependency}",
            border_style="cyan",
            expand=True,
        )
    )

    # Migration Steps Table
    steps_table = Table(
        title=f"Migration Transformation Steps ({len(res.proposal.migration_steps)} steps)",
        show_header=True,
        header_style="bold green",
        expand=True,
    )
    steps_table.add_column("#", width=4, justify="right")
    steps_table.add_column("Concrete Action Step", ratio=1)
    for idx, step in enumerate(res.proposal.migration_steps, start=1):
        steps_table.add_row(str(idx), step)
    console.print(steps_table)

    if res.proposal.shim_code:
        console.print(
            Panel(
                res.proposal.shim_code,
                title="Compatibility Shim Code",
                border_style="yellow",
            )
        )

    # Cryptographic Trust Ledger Receipt Table
    receipt_table = Table(
        title="Sacred Trust Ledger Receipt",
        show_header=True,
        header_style="bold magenta",
        expand=True,
    )
    receipt_table.add_column("Property", style="dim", width=18)
    receipt_table.add_column("Value")
    receipt_table.add_row("Claim ID", f"[bold]{res.claim.id}[/bold]")
    receipt_table.add_row("Trust Level", f"[yellow]{res.claim.trust_level.value}[/yellow]")
    receipt_table.add_row("Claim Statement", res.claim.statement)
    receipt_table.add_row("Evidence ID", f"[cyan]{res.evidence.id}[/cyan]")
    receipt_table.add_row("Evidence Path", f"{res.run_dir}/evidence/{res.evidence.path}")
    receipt_table.add_row("SHA256 Digest", f"[dim]{res.evidence.sha256}[/dim]")
    receipt_table.add_row("Reasoner Model", res.model_info.model_id)
    receipt_table.add_row(
        "Backend / Access",
        f"{res.model_info.backend} (is_mock={res.model_info.is_mock})",
    )
    console.print(receipt_table)


@app.command("sandbox")
def sandbox_cmd(
    repo_path_or_url: Annotated[
        str,
        typer.Argument(help="Path to local Python repository or remote git URL."),
    ],
    dep: Annotated[
        str,
        typer.Option("--dep", "-d", help="Target dependency to verify in sandbox."),
    ],
    python: Annotated[
        str,
        typer.Option("--python", "-p", help="Python version for sandbox (3.12 or 3.13)."),
    ] = "3.12",
    timeout: Annotated[
        float,
        typer.Option("--timeout", help="Timeout in seconds for test execution."),
    ] = 120.0,
    backend: Annotated[
        str | None,
        typer.Option("--backend", "-b", help="LLM backend: google | ollama | mock."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Output sandbox verification result as JSON."),
    ] = False,
    out: Annotated[
        Path | None,
        typer.Option("--out", "-o", help="Custom output directory for run evidence and reports."),
    ] = None,
) -> None:
    """Execute sandboxed baseline, migration, and differential testing with trust ledger updates."""
    valid_backends = ("google", "ollama", "mock")
    if backend and backend not in valid_backends:
        backends_str = ", ".join(valid_backends)
        console.print(
            f"[bold red]Invalid backend:[/bold red] '{backend}'. Supported backends: {backends_str}"
        )
        raise typer.Exit(1)

    try:
        b_lit = cast(Literal["google", "ollama", "mock"] | None, backend)
        res = run_sandbox_verification(
            repo_path=repo_path_or_url,
            dep_name=dep,
            python_version=python,
            timeout=timeout,
            backend=b_lit,
            run_dir=out,
        )
    except ModelError as exc:
        console.print(f"[bold red]Gemma Configuration Error:[/bold red] {exc}")
        console.print("\n[dim]To test without API keys, pass '--backend mock'.[/dim]")
        raise typer.Exit(1) from exc
    except Exception as exc:
        console.print(f"[bold red]Sandbox Error:[/bold red] {exc}")
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(res.model_dump(mode="json"), indent=2))
        return

    # Visual Rich Presentation
    status_style = "bold green" if res.verified else "bold red"
    verdict_text = "VERIFIED" if res.verified else "REFUTED"
    p_lvl = res.proposal_claim.trust_level.value
    r_lvl = res.resulting_claim.trust_level.value

    trans_str = f"[yellow]{p_lvl}[/yellow] -> [{status_style}]{r_lvl}[/{status_style}]"
    summary_text = (
        f"[bold]Target Dependency:[/bold]     {res.target_dependency}\n"
        f"[bold]Sandbox Python:[/bold]        {res.python_version}\n"
        f"[bold]Verification Verdict:[/bold]  [{status_style}]{verdict_text}[/{status_style}]\n"
        f"[bold]Ledger Transition:[/bold]     {trans_str}\n\n"
        f"[bold]Verdict Details:[/bold]\n{res.resulting_claim.note or 'N/A'}"
    )
    console.print(
        Panel(
            summary_text,
            title=f"ExitKit Sandbox Verification: {res.target_dependency}",
            border_style="green" if res.verified else "red",
            expand=True,
        )
    )

    # Test Execution Comparison Table
    test_table = Table(
        title="Test Execution Comparison (Baseline vs Migrated)",
        show_header=True,
        header_style="bold cyan",
        expand=True,
    )
    test_table.add_column("Stage", style="bold", width=12)
    test_table.add_column("Status", width=10, justify="center")
    test_table.add_column("Tests Ran", justify="right", width=10)
    test_table.add_column("Passed", justify="right", width=8)
    test_table.add_column("Failed", justify="right", width=8)
    test_table.add_column("Duration", justify="right", width=10)
    test_table.add_column("Evidence ID", ratio=1)

    b_status = "[green]PASS[/green]" if res.baseline.passed else "[bold red]FAIL[/bold red]"
    m_status = "[green]PASS[/green]" if res.migrated.passed else "[bold red]FAIL[/bold red]"

    test_table.add_row(
        "BASELINE",
        b_status,
        str(res.baseline.tests_ran),
        str(res.baseline.passed_count),
        str(res.baseline.failed_count + res.baseline.error_count),
        f"{res.baseline.duration_seconds}s",
        f"[dim]{res.baseline.evidence_id}[/dim]",
    )
    test_table.add_row(
        "MIGRATED",
        m_status,
        str(res.migrated.tests_ran),
        str(res.migrated.passed_count),
        str(res.migrated.failed_count + res.migrated.error_count),
        f"{res.migrated.duration_seconds}s",
        f"[dim]{res.migrated.evidence_id}[/dim]",
    )
    console.print(test_table)

    # Differential Test Results Table
    diff_table = Table(
        title=(
            f"Differential Parity Suite: {res.target_dependency} vs "
            f"{res.differential.replacement_dependency} "
            f"({res.differential.matched_cases}/{res.differential.total_cases} matched)"
        ),
        show_header=True,
        header_style="bold magenta",
        expand=True,
    )
    diff_table.add_column("Case Name", style="bold", width=26)
    diff_table.add_column("Parity", justify="center", width=8)
    diff_table.add_column("Input Repr", ratio=1)
    diff_table.add_column("Output Summary / Error", ratio=2)

    for case in res.differential.cases:
        p_str = "[green]MATCH[/green]" if case.matched else "[bold red]DIFF[/bold red]"
        out_summary = case.replacement_output or case.replacement_error or "N/A"
        diff_table.add_row(case.name, p_str, case.input_repr, out_summary)

    console.print(diff_table)

    # Trust Ledger Receipt
    receipt_table = Table(
        title="Sacred Trust Ledger Receipt",
        show_header=True,
        header_style="bold magenta",
        expand=True,
    )
    receipt_table.add_column("Property", style="dim", width=18)
    receipt_table.add_column("Value")
    receipt_table.add_row("Claim ID", f"[bold]{res.resulting_claim.id}[/bold]")
    receipt_table.add_row(
        "Trust Level",
        f"[{status_style}]{res.resulting_claim.trust_level.value}[/{status_style}]",
    )
    receipt_table.add_row("Claim Statement", res.resulting_claim.statement)
    receipt_table.add_row("Supersedes", str(res.resulting_claim.supersedes))
    receipt_table.add_row("Evidence IDs", ", ".join(res.resulting_claim.evidence_ids))
    receipt_table.add_row("Patch Evidence", res.patch_evidence_id)
    receipt_table.add_row("Run Directory", res.run_dir)
    console.print(receipt_table)


@app.command()
def compare(
    repo: Annotated[
        str,
        typer.Argument(
            help="Path to repository or local folder to analyze.",
        ),
    ],
    dep: Annotated[
        str,
        typer.Option(
            "--dep",
            "-d",
            help="Name of the dependency to compare and evaluate exit readiness for.",
        ),
    ],
    backend: Annotated[
        str | None,
        typer.Option(
            "--backend",
            "-b",
            help="LLM backend for gap triage: google, ollama, or mock.",
        ),
    ] = None,
    python: Annotated[
        str,
        typer.Option(
            "--python",
            "-p",
            help="Target Python version (3.12 or 3.13).",
        ),
    ] = "3.12",
    run_dir: Annotated[
        str | None,
        typer.Option(
            "--run-dir",
            help="Evidence run directory containing verification.json (or creates a new run).",
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Emit output as raw JSON.",
        ),
    ] = False,
) -> None:
    """Compare baseline vs migrated outputs, triage gaps with Gemma, and compute Exit Readiness."""
    target_path = Path(repo).resolve()
    if not target_path.exists():
        console.print(f"[bold red]Error:[/bold red] Repository path '{repo}' does not exist.")
        raise typer.Exit(code=1)

    effective_backend = cast(Literal["google", "ollama", "mock"], backend) if backend else None

    try:
        report = run_comparison(
            repo_path=target_path,
            dep_name=dep,
            backend=effective_backend,
            python_version=python,
            run_dir=run_dir,
        )
    except ModelError as err:
        console.print(f"[bold red]LLM Error:[/bold red] {err}")
        raise typer.Exit(code=1) from err
    except Exception as err:
        console.print(f"[bold red]Comparison Error:[/bold red] {err}")
        raise typer.Exit(code=1) from err

    if json_output:
        typer.echo(json.dumps(report.model_dump(mode="json"), indent=2))
        return

    # Render Terminal UI
    tier = report.breakdown.tier
    if tier == ReadinessTier.READY_FOR_EXIT:
        border_style = "green"
        tier_markup = "[bold green]READY_FOR_EXIT[/bold green]"
    elif tier == ReadinessTier.NEEDS_SHIM_OR_REVIEW:
        border_style = "yellow"
        tier_markup = "[bold yellow]NEEDS_SHIM_OR_REVIEW[/bold yellow]"
    else:
        border_style = "red"
        tier_markup = "[bold red]HIGH_RISK_GAPS[/bold red]"

    summary_text = (
        f"[bold]Target Dependency:[/bold] [cyan]{report.target_dependency}[/cyan]\n"
        f"[bold]Proposed Migration:[/bold] [magenta]{report.proposed_package}[/magenta]\n"
        f"[bold]Exit Readiness Score:[/bold] "
        f"[bold {border_style}]{report.readiness_score}/100[/bold {border_style}]\n"
        f"[bold]Recommendation Tier:[/bold] {tier_markup}\n"
        f"[bold]Run Directory:[/bold] [dim]{report.run_dir}[/dim]"
    )

    console.print(
        Panel(
            summary_text,
            title=f"ExitKit Readiness Assessment: {report.target_dependency}",
            border_style=border_style,
            expand=True,
        )
    )

    # Score Breakdown Table
    breakdown_table = Table(
        title="Exit Readiness Score Breakdown (Evidence-Only)",
        show_header=True,
        header_style="bold cyan",
        expand=True,
    )
    breakdown_table.add_column("Evaluation Criterion", style="bold", width=30)
    breakdown_table.add_column("Points", justify="right", width=10)
    breakdown_table.add_column("Max", justify="right", width=8)
    breakdown_table.add_column("Verification Basis / Details", ratio=1)

    bd = report.breakdown
    breakdown_table.add_row(
        "Test Suite Regression",
        str(bd.test_suite_score),
        "30",
        "Observed pass/fail counts in isolated virtualenv sandbox",
    )
    breakdown_table.add_row(
        "Differential Parity",
        str(bd.differential_score),
        "30",
        "Execution parity across side-by-side differential test cases",
    )
    breakdown_table.add_row(
        "Python 3.12 / 3.13 Support",
        str(bd.python_support_score),
        "20",
        "Dual-runtime execution check and installation capability",
    )
    breakdown_table.add_row(
        "API Surface Coverage",
        str(bd.api_surface_score),
        "20",
        "AST symbol coverage without unsupported API runtime calls",
    )
    breakdown_table.add_row(
        "Gap Penalties",
        str(bd.gap_penalties),
        "0",
        "Deductions for blockers (-30), high (-15), medium (-5), low (-2)",
    )
    breakdown_table.add_row(
        "[bold]TOTAL EXIT READINESS[/bold]",
        f"[bold {border_style}]{bd.total_score}[/bold {border_style}]",
        "100",
        f"[bold]{bd.tier.value}[/bold] ({bd.explanation})",
    )
    console.print(breakdown_table)

    # Compatibility Gaps Table
    if report.gaps:
        gap_table = Table(
            title=f"Discovered Compatibility Gaps ({len(report.gaps)})",
            show_header=True,
            header_style="bold red",
            expand=True,
        )
        gap_table.add_column("Gap ID", style="bold", width=16)
        gap_table.add_column("Category", width=22)
        gap_table.add_column("Severity", justify="center", width=12)
        gap_table.add_column("Description", ratio=2)
        gap_table.add_column("Affected Symbol / File", ratio=1)
        gap_table.add_column("Evidence ID", width=16)

        for g in report.gaps:
            sev_color = "bold red" if g.severity == "BLOCKER" else "yellow"
            sym_file = f"{g.affected_file or ''} {g.affected_symbol or ''}".strip() or "N/A"
            gap_table.add_row(
                g.id,
                g.category.value,
                f"[{sev_color}]{g.severity.value}[/{sev_color}]",
                g.description,
                sym_file,
                f"[dim]{g.evidence_id}[/dim]",
            )
        console.print(gap_table)

    # Honest Limitations Table
    if report.limitations:
        lim_table = Table(
            title=f"Honest Limitations Disclosed ({len(report.limitations)})",
            show_header=True,
            header_style="bold yellow",
            expand=True,
        )
        lim_table.add_column("Limitation ID", style="bold", width=22)
        lim_table.add_column("Category", width=20)
        lim_table.add_column("Scope", width=16)
        lim_table.add_column("Description", ratio=1)

        for lim in report.limitations:
            lim_table.add_row(
                lim.id,
                lim.category.value,
                lim.affected_scope or "N/A",
                lim.description,
            )
        console.print(lim_table)

    # Gemma Gap Triage Table
    if report.triages:
        triage_table = Table(
            title=f"Gemma 4 Compatibility Gap Triage ({len(report.triages)})",
            show_header=True,
            header_style="bold magenta",
            expand=True,
        )
        triage_table.add_column("Gap ID", style="bold", width=16)
        triage_table.add_column("Root Cause Analysis", ratio=2)
        triage_table.add_column("Suggested Fix / Shim", ratio=2)
        triage_table.add_column("Claim ID", width=22)

        for tr in report.triages:
            triage_table.add_row(
                tr.gap_id,
                tr.cause,
                tr.suggested_fix,
                f"[dim]{tr.claim.id}[/dim]",
            )
        console.print(triage_table)


if __name__ == "__main__":
    app()
