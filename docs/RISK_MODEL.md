# ExitKit Deterministic Risk Model

> **Philosophy:** No fuzzy heuristics, no generative AI hallucinations. Every risk score is computed through transparent, deterministic signals strictly linked to cryptographically verified execution receipts.

---

## 1. Overview of Risk Signals

ExitKit evaluates every discovered dependency across multiple independent verification vectors:
1. **Isolated Smoke Tests**: Running real installs and imports on target Python versions.
2. **Registry Metadata (PyPI)**: Checking release history, yanked status, inactive classifiers, and declared version support.
3. **Upstream Source Health (GitHub)**: Querying repository status for deprecation or abandonment.
4. **Known Vulnerabilities (OSV.dev)**: Scanning for published security advisories.
5. **Manifest Divergence**: Detecting conflicting requirements declared across multiple project files.

A dependency's total **Risk Score** is the exact sum of all triggered signal points.

---

## 2. Signal Scoring Matrix

| Signal Name | Points | Trigger Condition | Backing Evidence Type |
| :--- | :---: | :--- | :--- |
| `IMPORT_FAILS_PY312` | 35 | Dependency installs but fails `python -c "import <mod>"` on Python 3.12 | `COMMAND_OUTPUT` (terminal output & exit code) |
| `IMPORT_FAILS_PY313` | 35 | Dependency installs but fails `python -c "import <mod>"` on Python 3.13 | `COMMAND_OUTPUT` (terminal output & exit code) |
| `INSTALL_FAILS_PY312` | 30 | Package fails to install via `uv pip install` on Python 3.12 | `COMMAND_OUTPUT` (resolver or build failure log) |
| `INSTALL_FAILS_PY313` | 30 | Package fails to install via `uv pip install` on Python 3.13 | `COMMAND_OUTPUT` (resolver or build failure log) |
| `YANKED_VERSION_IN_USE` | 25 | The declared or resolved release has been yanked from PyPI | `HTTP_RESPONSE` (PyPI release JSON payload) |
| `REPO_ARCHIVED` | 25 | Upstream GitHub repository is marked archived (read-only) | `HTTP_RESPONSE` (GitHub repository API payload) |
| `INACTIVE_CLASSIFIER` | 25 | Package carries `Development Status :: 7 - Inactive` on PyPI | `HTTP_RESPONSE` (PyPI package metadata JSON) |
| `MANIFEST_DIVERGENCE (Incompatible)` | 20 | Conflicting, non-overlapping constraints in manifests (e.g. `>=2` vs `==1.10.13`) | `FILE_SNAPSHOT` / `HTTP_RESPONSE` |
| `MANIFEST_DIVERGENCE (Compatible)` | 5 | Differing but overlapping specifiers across manifests | `FILE_SNAPSHOT` / `HTTP_RESPONSE` |
| `NO_PY312_OR_PY313_SUPPORT` | 15 | PyPI metadata lacks both Python 3.12 and Python 3.13 classifiers | `HTTP_RESPONSE` (PyPI classifiers list) |
| `STALE_LATEST_RELEASE` | 15 | Latest release on PyPI is older than 3 years (1,095 days) | `HTTP_RESPONSE` (PyPI upload timestamps) |
| `PINNED_VERSION_OLD` | 10 | The specific version in use was released more than 3 years ago | `HTTP_RESPONSE` (PyPI release timestamps) |
| `PINNED_BEHIND_LATEST` | 10 | Pinned version is at least one major version behind current release | `HTTP_RESPONSE` (PyPI latest vs resolved) |
| `KNOWN_VULNERABILITIES` | 10–40 | Published security advisories on OSV.dev (scaled by severity up to 40 pts) | `HTTP_RESPONSE` (OSV.dev query response) |
| `NON_PYPI_SOURCE` | 0 | Non-PyPI dependency (git, local URL, editable); informational | `FILE_SNAPSHOT` (manifest line) |

---

## 3. Handling Unavailable Signals

If an external service is unreachable or rate-limited:
- The signal status is recorded as `SignalStatus.UNAVAILABLE`.
- It awards **0 points** to the risk score.
- An `UNVERIFIED` claim is generated with an explicit reason explaining why verification could not be performed.
- ExitKit **never guesses** or defaults to assuming an unverified dependency is safe.

---

## 4. Traceability to the Sacred Trust Ledger

Every triggered signal produces a corresponding `Claim` in the trust ledger:
- **Available Signals**: Tagged as `TrustLevel.OBSERVED`, origin `ClaimSource.TOOL`, pointing to the SHA-256 hash of the evidence file saved under `.exitkit/runs/<run_id>/evidence/`.
- **Unavailable Signals**: Tagged as `TrustLevel.UNVERIFIED`, origin `ClaimSource.TOOL`, with a mandatory explanation note.
