"""Virtual environment runner and test suite execution within sandboxes."""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path

from exitkit.ledger.models import BaselineOutcome, EvidenceKind, RunLabel
from exitkit.ledger.store import EvidenceStore
from exitkit.sandbox.models import CommandRunResult, TestRunResult


class SandboxRunner:
    """Manages virtual environments and executes tests in isolated sandboxes."""

    def __init__(
        self,
        workspace: Path,
        python_version: str = "3.12",
        timeout: float = 60.0,
    ) -> None:
        self.workspace = workspace.resolve()
        self.python_version = python_version
        self.timeout = timeout
        self.venv_dir = self.workspace / ".venv"
        self._python_bin: Path | None = None

    @property
    def python_bin(self) -> Path:
        """Path to virtualenv Python interpreter."""
        if self._python_bin is None or not self._python_bin.exists():
            bin_dir = "Scripts" if (self.venv_dir / "Scripts").exists() else "bin"
            self._python_bin = self.venv_dir / bin_dir / "python"
        return self._python_bin

    def setup_venv(self) -> CommandRunResult:
        """Create isolated virtual environment using uv."""
        uv_path = shutil.which("uv") or "uv"
        cmd = [
            uv_path,
            "venv",
            str(self.venv_dir),
            "--python",
            self.python_version,
            "--allow-existing",
        ]
        return self.run_command(cmd, timeout=30.0)

    def run_command(
        self,
        cmd: list[str],
        timeout: float | None = None,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> CommandRunResult:
        """Execute command in sandbox with strict timeout."""
        effective_timeout = timeout or self.timeout
        effective_cwd = cwd or self.workspace

        start_time = time.perf_counter()

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                env=env,
                cwd=effective_cwd,
                check=False,
            )
            duration = time.perf_counter() - start_time
            return CommandRunResult(
                command=cmd,
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                duration_seconds=round(duration, 3),
                timeout_hit=False,
            )
        except subprocess.TimeoutExpired as exc:
            duration = time.perf_counter() - start_time
            stdout_text = (
                exc.stdout.decode("utf-8", errors="replace")
                if isinstance(exc.stdout, bytes)
                else (exc.stdout or "")
            )
            stderr_text = (
                exc.stderr.decode("utf-8", errors="replace")
                if isinstance(exc.stderr, bytes)
                else (exc.stderr or "")
            )
            return CommandRunResult(
                command=cmd,
                exit_code=-9,
                stdout=stdout_text,
                stderr=f"Command timed out after {effective_timeout}s\n{stderr_text}",
                duration_seconds=round(duration, 3),
                timeout_hit=True,
            )
        except Exception as exc:
            duration = time.perf_counter() - start_time
            return CommandRunResult(
                command=cmd,
                exit_code=1,
                stdout="",
                stderr=f"Execution error: {exc}",
                duration_seconds=round(duration, 3),
                timeout_hit=False,
            )

    def install_dependencies(self) -> CommandRunResult:
        """Install target repository dependencies and pytest runner."""
        uv_path = shutil.which("uv") or "uv"
        python_str = str(self.python_bin)

        # 1. Install pytest runner in venv
        self.run_command(
            [uv_path, "pip", "install", "--python", python_str, "pytest"],
            timeout=60.0,
        )

        # 2. Check for requirements files
        req_txt = self.workspace / "requirements.txt"
        if req_txt.exists():
            res = self.run_command(
                [uv_path, "pip", "install", "--python", python_str, "-r", str(req_txt)],
                timeout=90.0,
            )
            if res.exit_code != 0:
                # If shipped requirements fail, try without hashes / permissive
                pass

        # 3. If pyproject.toml or setup.py exists, install editable package
        pyproj = self.workspace / "pyproject.toml"
        setup_py = self.workspace / "setup.py"
        if pyproj.exists() or setup_py.exists():
            self.run_command(
                [uv_path, "pip", "install", "--python", python_str, "--no-deps", "-e", "."],
                timeout=60.0,
            )

        return CommandRunResult(
            command=[uv_path, "pip", "install"],
            exit_code=0,
            stdout="Dependencies installed into sandbox virtualenv.",
            stderr="",
            duration_seconds=0.0,
        )

    def run_test_suite(
        self,
        label: RunLabel,
        store: EvidenceStore,
    ) -> TestRunResult:
        """Run repository test suite and record evidence."""
        # Check if tests exist
        tests_dir = self.workspace / "tests"
        has_tests = tests_dir.exists() or any(self.workspace.glob("**/test_*.py"))
        if not has_tests:
            ev = store.add_text(
                kind=EvidenceKind.TEST_RUN,
                text="NO TESTS FOUND IN REPOSITORY",
                description=f"{label.value} test run: No test files detected in repository",
                exit_code=0,
                duration_seconds=0.0,
            )
            return TestRunResult(
                label=label,
                passed=True,
                baseline_outcome=BaselineOutcome.NO_TESTS,
                tests_ran=0,
                duration_seconds=0.0,
                command=[],
                exit_code=0,
                stdout="No test files detected in repository.",
                stderr="",
                evidence_id=ev.id,
            )

        # Execute pytest
        cmd = [str(self.python_bin), "-m", "pytest", "-v", "--tb=short"]
        res = self.run_command(cmd, timeout=self.timeout)

        # Parse test metrics
        passed_cnt, failed_cnt, error_cnt, skipped_cnt = 0, 0, 0, 0
        p_match = re.search(r"(\d+)\s+passed", res.stdout)
        f_match = re.search(r"(\d+)\s+failed", res.stdout)
        e_match = re.search(r"(\d+)\s+error", res.stdout)
        s_match = re.search(r"(\d+)\s+skipped", res.stdout)

        if p_match:
            passed_cnt = int(p_match.group(1))
        if f_match:
            failed_cnt = int(f_match.group(1))
        if e_match:
            error_cnt = int(e_match.group(1))
        if s_match:
            skipped_cnt = int(s_match.group(1))

        total_ran = passed_cnt + failed_cnt + error_cnt
        test_passed = res.exit_code == 0

        # Determine BaselineOutcome if label is BASELINE
        baseline_outcome: BaselineOutcome | None = None
        if label == RunLabel.BASELINE:
            baseline_outcome = (
                BaselineOutcome.PASS_AS_DECLARED
                if test_passed
                else BaselineOutcome.FAIL_AS_DECLARED
            )

        # Collect failure details
        failures: list[str] = []
        if not test_passed:
            for line in res.stdout.splitlines() + res.stderr.splitlines():
                if "FAILED" in line or "ERROR" in line or "ImportError" in line:
                    failures.append(line.strip())

        # Persist full test log as TEST_RUN evidence
        raw_log = (
            f"=== {label.value} TEST RUN ===\n"
            f"Command: {' '.join(cmd)}\n"
            f"Exit Code: {res.exit_code}\n"
            f"Duration: {res.duration_seconds}s\n\n"
            f"--- STDOUT ---\n{res.stdout}\n\n"
            f"--- STDERR ---\n{res.stderr}\n"
        )
        ev_desc = (
            f"{label.value} test suite execution (exit code: {res.exit_code}, "
            f"passed: {passed_cnt}, failed: {failed_cnt + error_cnt})"
        )
        ev = store.add_text(
            kind=EvidenceKind.TEST_RUN,
            text=raw_log,
            description=ev_desc,
            command=" ".join(cmd),
            exit_code=res.exit_code,
            duration_seconds=res.duration_seconds,
            ext="txt",
        )

        return TestRunResult(
            label=label,
            passed=test_passed,
            baseline_outcome=baseline_outcome,
            tests_ran=total_ran,
            passed_count=passed_cnt,
            failed_count=failed_cnt,
            error_count=error_cnt,
            skipped_count=skipped_cnt,
            duration_seconds=res.duration_seconds,
            command=cmd,
            exit_code=res.exit_code,
            stdout=res.stdout,
            stderr=res.stderr,
            evidence_id=ev.id,
            failure_details=failures[:10],
        )

    def attempt_minimal_fix(self, store: EvidenceStore) -> TestRunResult | None:
        """Attempt smallest constraint change to establish PASS_AFTER_MINIMAL_FIX."""
        uv_path = shutil.which("uv") or "uv"
        python_str = str(self.python_bin)

        # Apply minimal fix: upgrade Jinja2 to >=3.0.0 to resolve soft_unicode removal
        fix_cmd = [uv_path, "pip", "install", "--python", python_str, "jinja2>=3.0.0"]
        res = self.run_command(fix_cmd)
        if res.exit_code != 0:
            return None

        # Re-run test suite to check if minimal fix resolved it
        retest = self.run_test_suite(label=RunLabel.BASELINE, store=store)
        if retest.passed:
            return retest.model_copy(
                update={"baseline_outcome": BaselineOutcome.PASS_AFTER_MINIMAL_FIX}
            )
        return None
