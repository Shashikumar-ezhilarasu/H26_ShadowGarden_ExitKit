"""Command-line interface for ExitKit."""

from __future__ import annotations

import shutil
import sys
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from exitkit import __version__
from exitkit.config import check_env_status

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


if __name__ == "__main__":
    app()
