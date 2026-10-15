# Target Inspection Notes: LinkWatch

> Target repository: [https://github.com/rishrek/LinkWatch](https://github.com/rishrek/LinkWatch)  
> Target cloned locally to: `.targets/LinkWatch` (gitignored)

This document provides a technical assessment of the organizer's target repository (`LinkWatch`), its dependency declarations, test infrastructure, Python version constraints, compatibility issues, and risk profiles.

---

## 1. Dependency Declarations & Manifest Divergence

LinkWatch declares its dependencies across three separate files, with noticeable conflicts and discrepancies:

### Manifest Discrepancies
| Package | `pyproject.toml` | `requirements.txt` | `requirements-dev.txt` | Notes |
| :--- | :--- | :--- | :--- | :--- |
| `pydantic` | `pydantic>=2` | `pydantic==1.10.13` | — | **Divergence**: `pyproject.toml` requests V2, while `requirements.txt` pins legacy V1. |
| `Jinja2` | `Jinja2==2.11.3` | `Jinja2==2.11.3` | — | **Broken on Python 3.12**: imports removed `markupsafe.soft_unicode`. |
| `toml` | `toml==0.10.2` | `toml==0.10.2` | — | Unmaintained since 2020. Built-in `tomllib` available in Python 3.11+. |
| `appdirs` | `appdirs==1.4.4` | `appdirs==1.4.4` | — | Unmaintained since 2020. Standard modern replacement is `platformdirs`. |
| `nose` | — | — | `nose==1.3.7` | Dead project since 2015. Fails to run on Python 3.12+ due to removed `imp` module. |
| `six` | `six` | `six` | — | Obsolete Python 2/3 compatibility bridge. |
| `idna` | `idna==2.7` | `idna==2.7` | — | Outdated release (2018). |
| `setuptools` | `setuptools==75.8.0` | `setuptools==75.8.0` | — | Used at runtime for `pkg_resources`, which is deprecated. |
| `PyYAML` | — | `# PyYAML==5.1` | — | Commented out in `requirements.txt`. |

---

## 2. Python Version Constraints

- `pyproject.toml` declares: `requires-python = ">=3.10"`.
- `Makefile` defaults to `PYTHON ?= python3.12`.
- **Python 3.12 & 3.13 Reality Check**:
  - `nose==1.3.7` depends on the standard library `imp` module, which was completely removed in Python 3.12 (PEP 594). Executing `make test-nose` or `nosetests` fails immediately.
  - `Jinja2==2.11.3` fails during import on modern Python environments when standard modern `markupsafe` (>=2.1) is installed, producing:
    ```
    ImportError: cannot import name 'soft_unicode' from 'markupsafe'
    ```
  - When modern `jinja2>=3.0` is provided, all 10 pytest tests pass cleanly.

---

## 3. How Tests Run

LinkWatch contains 10 pytest test cases across 4 test modules in `tests/`:
1. `tests/test_cache.py` (1 test): Validates filesystem cache serialization (`save_results` and `load_results`).
2. `tests/test_checker.py` (7 tests): Spawns a background `ThreadingHTTPServer` to verify HTTP checks (200 OK, 404 missing, 302 redirects, slow responses, and closed ports).
3. `tests/test_config.py` (1 test): Validates TOML parsing into Pydantic models from `sites.toml`.
4. `tests/test_report.py` (1 test): Tests Jinja2 HTML report generation using Click's `CliRunner`.

### Test Execution Commands
- Primary runner: `python -m pytest` or `uv run pytest`.
- Legacy runner: `nosetests tests/` (referenced in `Makefile:test-nose`, but broken on Python 3.12+).

---

## 4. Code Architecture & Runtime Peculiarities

1. **Monkey Patching at Import Time (`linkwatch/http_patch.py`)**:
   `linkwatch.cli` imports `http_patch`, which mutates `requests.Session.request` at module load time to inject a custom `User-Agent: linkwatch/1.0`. Any replacement HTTP client or shim must account for or preserve this behavior.
2. **Pydantic V1 Deprecation Warnings on V2**:
   `linkwatch/config.py` uses Pydantic V1 syntax:
   - `@validator("name")` and `@validator("url")`
   - `class Config: anystr_strip_whitespace = True`
   On Pydantic V2, this emits deprecation warnings advising migration to `@field_validator` and `ConfigDict(str_strip_whitespace=True)`.
3. **`pkg_resources` Deprecation (`linkwatch/resources.py`)**:
   Uses `pkg_resources.resource_filename("linkwatch", "templates")`, which triggers `DeprecationWarning: pkg_resources is deprecated as an API`. Should be migrated to `importlib.resources`.
4. **Cache Directory Resolution (`linkwatch/cache.py`)**:
   Uses `appdirs.user_cache_dir("linkwatch")` or environment override `LINKWATCH_CACHE_DIR`.

---

## 5. Prime Migration Candidates for ExitKit

The target repo exhibits classic real-world dependency rot ideal for ExitKit's Dependency Exit Capsules:
1. **`Jinja2==2.11.3`**: High priority; currently crashing `test_report.py` due to `soft_unicode`.
2. **`appdirs==1.4.4`**: Abandoned package; drop-in replacement with `platformdirs`.
3. **`toml==0.10.2`**: Unmaintained; direct replacement with standard library `tomllib`.
4. **`nose==1.3.7`**: Broken test dependency on Python 3.12+; fully superseded by `pytest`.
5. **`setuptools` / `pkg_resources`**: Runtime dependency that can be eliminated in favor of Python's built-in `importlib.resources`.
