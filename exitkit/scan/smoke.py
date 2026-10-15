"""Isolated venv smoke testing for installability and importability."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from packaging.utils import canonicalize_name

from exitkit.ledger.models import Evidence, EvidenceKind, SignalStatus
from exitkit.ledger.store import EvidenceStore

KNOWN_IMPORT_MAP: dict[str, str] = {
    "pyyaml": "yaml",
    "jinja2": "jinja2",
    "python-dateutil": "dateutil",
    "appdirs": "appdirs",
    "pydantic": "pydantic",
    "pillow": "PIL",
    "beautifulsoup4": "bs4",
    "setuptools": "setuptools",
    "click": "click",
    "requests": "requests",
    "rich": "rich",
    "toml": "toml",
    "chardet": "chardet",
    "six": "six",
    "idna": "idna",
    "nose": "nose",
    "pytest": "pytest",
}


@dataclass(frozen=True)
class SmokeResult:
    """Outcome of an isolated install + import smoke test."""

    status: SignalStatus
    python_version: str
    install_passed: bool = False
    import_passed: bool = False
    installed_version: str | None = None
    import_name: str | None = None
    install_evidence: Evidence | None = None
    import_evidence: Evidence | None = None
    unavailable_reason: str | None = None
    error_summary: str | None = None


def resolve_import_name(package_name: str) -> str:
    """Resolve Python module import name for a given package name."""
    cname = canonicalize_name(package_name)
    return KNOWN_IMPORT_MAP.get(cname, cname.replace("-", "_"))


def is_python_available(py_version: str) -> bool:
    """Check if the given Python version is available via uv or PATH."""
    try:
        res = subprocess.run(
            ["uv", "python", "find", py_version],
            capture_output=True,
            text=True,
            check=False,
        )
        return res.returncode == 0
    except Exception:
        return bool(shutil.which(f"python{py_version}"))


def run_smoke_test(
    package_name: str,
    specifier: str,
    py_version: str,
    evidence_store: EvidenceStore,
    timeout: float = 180.0,
) -> SmokeResult:
    """Run an isolated install and import smoke test in a throwaway venv."""
    if not is_python_available(py_version):
        return SmokeResult(
            status=SignalStatus.UNAVAILABLE,
            python_version=py_version,
            unavailable_reason=f"Python {py_version} interpreter is not available on system",
        )

    install_target = f"{package_name}{specifier}" if specifier else package_name
    import_module = resolve_import_name(package_name)

    with tempfile.TemporaryDirectory(prefix=f"exitkit_smoke_{py_version}_") as tmp_dir:
        tmp_path = Path(tmp_dir).resolve()
        venv_path = tmp_path / "venv"

        # 1. Create throwaway venv
        venv_cmd = ["uv", "venv", str(venv_path), "--python", py_version]
        try:
            res_venv = subprocess.run(
                venv_cmd,
                cwd=str(tmp_path),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if res_venv.returncode != 0:
                err_msg = f"Failed to create uv venv on Python {py_version}: {res_venv.stderr}"
                return SmokeResult(
                    status=SignalStatus.UNAVAILABLE,
                    python_version=py_version,
                    unavailable_reason=err_msg,
                )
        except Exception as exc:
            return SmokeResult(
                status=SignalStatus.UNAVAILABLE,
                python_version=py_version,
                unavailable_reason=f"Venv creation timed out or failed: {exc}",
            )

        py_bin = venv_path / "bin" / "python"
        if not py_bin.exists():
            return SmokeResult(
                status=SignalStatus.UNAVAILABLE,
                python_version=py_version,
                unavailable_reason="Virtualenv python binary missing",
            )

        # 2. Install dependency with exact declared specifier
        t0 = time.perf_counter()
        inst_cmd = ["uv", "pip", "install", "--python", str(py_bin), install_target]
        try:
            res_inst = subprocess.run(
                inst_cmd,
                cwd=str(tmp_path),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            duration_inst = round(time.perf_counter() - t0, 3)
            inst_log = (
                f"$ {' '.join(inst_cmd)}\n"
                f"[exit code: {res_inst.returncode}, duration: {duration_inst}s]\n\n"
                f"STDOUT:\n{res_inst.stdout}\n\nSTDERR:\n{res_inst.stderr}"
            )
            inst_ev = evidence_store.add_text(
                kind=EvidenceKind.COMMAND_OUTPUT,
                text=inst_log,
                description=f"Install {install_target} on Python {py_version}",
                command=" ".join(inst_cmd),
                exit_code=res_inst.returncode,
                duration_seconds=duration_inst,
            )
        except Exception as exc:
            return SmokeResult(
                status=SignalStatus.UNAVAILABLE,
                python_version=py_version,
                unavailable_reason=f"Installation failed to execute: {exc}",
            )

        if res_inst.returncode != 0:
            return SmokeResult(
                status=SignalStatus.AVAILABLE,
                python_version=py_version,
                install_passed=False,
                import_passed=False,
                install_evidence=inst_ev,
                error_summary=f"Installation failed with code {res_inst.returncode}",
            )

        # 3. Read installed version
        installed_ver: str | None = None
        try:
            ver_cmd = [
                str(py_bin),
                "-c",
                (
                    "import importlib.metadata as m; "
                    f"print(m.version('{canonicalize_name(package_name)}'))"
                ),
            ]
            res_ver = subprocess.run(
                ver_cmd,
                cwd=str(tmp_path),
                capture_output=True,
                text=True,
                timeout=15,
            )
            if res_ver.returncode == 0:
                installed_ver = res_ver.stdout.strip()
        except Exception:
            pass

        # 4. Import smoke test
        t1 = time.perf_counter()
        imp_cmd = [str(py_bin), "-c", f"import {import_module}"]
        try:
            res_imp = subprocess.run(
                imp_cmd,
                cwd=str(tmp_path),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            duration_imp = round(time.perf_counter() - t1, 3)
            imp_log = (
                f"$ {' '.join(imp_cmd)}\n"
                f"[exit code: {res_imp.returncode}, duration: {duration_imp}s]\n\n"
                f"STDOUT:\n{res_imp.stdout}\n\nSTDERR:\n{res_imp.stderr}"
            )
            imp_ev = evidence_store.add_text(
                kind=EvidenceKind.COMMAND_OUTPUT,
                text=imp_log,
                description=f"Smoke import {import_module} on Python {py_version}",
                command=" ".join(imp_cmd),
                exit_code=res_imp.returncode,
                duration_seconds=duration_imp,
            )
        except Exception as exc:
            return SmokeResult(
                status=SignalStatus.UNAVAILABLE,
                python_version=py_version,
                install_passed=True,
                installed_version=installed_ver,
                install_evidence=inst_ev,
                unavailable_reason=f"Import test failed to execute: {exc}",
            )

        import_ok = res_imp.returncode == 0
        error_summary = None
        if not import_ok:
            # Extract key exception line from stderr
            err_lines = [ln.strip() for ln in res_imp.stderr.splitlines() if ln.strip()]
            error_summary = (
                err_lines[-1] if err_lines else f"Import failed with exit code {res_imp.returncode}"
            )

        return SmokeResult(
            status=SignalStatus.AVAILABLE,
            python_version=py_version,
            install_passed=True,
            import_passed=import_ok,
            installed_version=installed_ver,
            import_name=import_module,
            install_evidence=inst_ev,
            import_evidence=imp_ev,
            error_summary=error_summary,
        )


def run_repo_dry_run_resolver(
    requirements: list[str],
    py_version: str,
    evidence_store: EvidenceStore,
    timeout: float = 180.0,
) -> tuple[SignalStatus, bool, Evidence | None, str | None]:
    """Run `uv pip install --dry-run` to verify repository resolver compatibility."""
    if not is_python_available(py_version):
        return (
            SignalStatus.UNAVAILABLE,
            False,
            None,
            f"Python {py_version} interpreter not available",
        )

    if not requirements:
        return SignalStatus.AVAILABLE, True, None, None

    with tempfile.TemporaryDirectory(prefix=f"exitkit_dryrun_{py_version}_") as tmp_dir:
        tmp_path = Path(tmp_dir).resolve()
        cmd = ["uv", "pip", "install", "--dry-run", f"--python={py_version}", *requirements]

        t0 = time.perf_counter()
        try:
            res = subprocess.run(
                cmd,
                cwd=str(tmp_path),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            duration = round(time.perf_counter() - t0, 3)
            log = (
                f"$ {' '.join(cmd)}\n"
                f"[exit code: {res.returncode}, duration: {duration}s]\n\n"
                f"STDOUT:\n{res.stdout}\n\nSTDERR:\n{res.stderr}"
            )
            desc = f"Dry-run resolver check across repo requirements on Python {py_version}"
            ev = evidence_store.add_text(
                kind=EvidenceKind.COMMAND_OUTPUT,
                text=log,
                description=desc,
                command=" ".join(cmd),
                exit_code=res.returncode,
                duration_seconds=duration,
            )
            passed = res.returncode == 0
            err_summary = None if passed else f"Resolver dry-run failed with code {res.returncode}"
            return SignalStatus.AVAILABLE, passed, ev, err_summary
        except Exception as exc:
            return (
                SignalStatus.UNAVAILABLE,
                False,
                None,
                f"Resolver dry-run execution failed: {exc}",
            )
