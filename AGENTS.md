# ExitKit Agent Guidelines & Project Rules

ExitKit is an open-source Python CLI that analyses a target Python repo, finds at-risk dependencies, proposes a replacement using Gemma 4, VERIFIES it by actually running code, and emits a reusable "Dependency Exit Capsule". Tagline: "Don't trust the AI's migration plan. Trust the receipt."

## Hard Rules
1. Python 3.12 and 3.13 only. Never assume 3.14. `requires-python = ">=3.12,<3.14"`. CI matrix runs both.
2. Never hardcode, fake, or pre-write analysis output for any target repo. All results must come from real scanning, real execution, real model calls. Test fixtures are allowed only inside tests/ and must never appear in demo output.
3. The trust ledger is sacred. Every claim in a capsule carries a trust level: `OBSERVED`, `AI_PROPOSED`, `AI_PROPOSED_VERIFIED`, `AI_PROPOSED_REFUTED`. A claim may be `OBSERVED` only if it references an evidence file that exists and whose sha256 matches. The schema must reject anything else.
4. Gemma 4 is a bounded reasoner. Its model id comes from env `EXITKIT_GEMMA_MODEL` (never hardcode a model id). Supported backends: Google GenAI API, Ollama. A "mock" backend exists for unit tests only and must stamp every output "MOCK" so it can never pass as real.
5. Never modify the target repo in place. All execution happens in a temp copy or git worktree with timeouts. Never run anything outside the sandbox directory.
6. Preserve existing functionality. Before finishing any step, the full test suite must pass, including tests from earlier steps. Never delete or weaken a test to make it pass.
7. If a check fails or something cannot be done, say so plainly in the report. Do not paper over it.
8. Code quality: typed, small modules, ruff + mypy clean, docstrings on public functions, no dead code, no giant files. Write like a human maintainer, not a generator: clear names, minimal comments that explain why.

## Layout
`exitkit/{cli.py,config.py,ledger/,discover/,scan/,usage/,llm/,sandbox/,compare/,capsule/,report/}`, `tests/`, `docs/`, `examples/`. Use typer + rich, pydantic v2, httpx, packaging, uv, pytest, jinja2.

## Workflow (mandatory)
- Work on ONE step at a time, exactly the step given. Do not start the next step.
- At the end of a step: run ruff, mypy, pytest, plus the step's acceptance commands. Then print a block titled "VERIFICATION REPORT" with:
  (a) every command run and its real output (trimmed)
  (b) files created or changed (distinguishing pre-existing modified vs newly created)
  (c) what is NOT done or is shaky
  (d) the suggested commit message. Then STOP and wait for confirmation.
- Do not commit until the user says "commit". When approved, run git add -A and git commit with the suggested message, then show git log --oneline -3 and git push.

## Findings about the target repo (from Step 0)
- LinkWatch's test suite FAILS as shipped on Python 3.12 (Jinja2==2.11.3 breaks against modern markupsafe: ImportError soft_unicode). It passes when Jinja2 >= 3.0 is used.
- Therefore the pipeline must never assume a green baseline. Baseline has three recorded outcomes: PASS_AS_DECLARED, FAIL_AS_DECLARED (captured as OBSERVED evidence, with the exact error), and PASS_AFTER_MINIMAL_FIX (the smallest constraint change that makes tests run).
- A proposal may be one of: upgrade (same package, newer version), replace (different package), or remove/vendor. The proposer must consider upgrade first and justify any replacement over it.
- The risk scan must include an OBSERVED import smoke test per dependency (install + import on Python 3.12 and 3.13), since that is exactly what exposed this failure.
