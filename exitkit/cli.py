"""Command-line interface for ExitKit."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from exitkit import __version__
from exitkit.config import check_env_status
from exitkit.ledger.models import Capsule
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


if __name__ == "__main__":
    app()
