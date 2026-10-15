"""Parsers for project manifest files and dependency discovery."""

from __future__ import annotations

import ast
import configparser
import re
import tomllib
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from exitkit.discover.models import (
    DeclaredSpecifierRecord,
    DependencyScope,
    DiscoveredDependency,
    DiscoveryResult,
    ManifestDivergence,
)

COMMENTED_PIN_REGEX = re.compile(r"^\s*#\s*([a-zA-Z0-9_\-\.]+)\s*([=!><~^].*)$")


def _check_specifiers_compatible(spec_strings: list[str]) -> tuple[bool, str]:
    """Determine whether a set of specifier strings are mutually compatible."""
    clean_specs = [s.strip() for s in spec_strings if s.strip()]
    if len(clean_specs) <= 1:
        return True, "Single or identical specifier"

    try:
        combined = SpecifierSet()
        for s in clean_specs:
            combined &= SpecifierSet(s)
    except Exception as exc:
        return False, f"Incompatible specifier syntax: {exc}"

    exact_pins: list[Version] = []
    max_lower: Version | None = None
    max_lower_strict: bool = False
    min_upper: Version | None = None
    min_upper_strict: bool = False

    for spec in combined:
        op = spec.operator
        try:
            ver = Version(spec.version)
        except Exception:
            continue

        if op in ("==", "==="):
            exact_pins.append(ver)
        elif op == ">=":
            if max_lower is None or ver > max_lower:
                max_lower = ver
                max_lower_strict = False
        elif op == ">":
            if max_lower is None or ver >= max_lower:
                max_lower = ver
                max_lower_strict = True
        elif op == "<=":
            if min_upper is None or ver < min_upper:
                min_upper = ver
                min_upper_strict = False
        elif op == "<":
            if min_upper is None or ver <= min_upper:
                min_upper = ver
                min_upper_strict = True
        elif op == "~=":
            if max_lower is None or ver > max_lower:
                max_lower = ver
                max_lower_strict = False
            # Next major/minor bump for ~= operator
            if len(ver.release) <= 2:
                upper_ver = Version(f"{ver.major + 1}.0")
            else:
                upper_ver = Version(f"{ver.major}.{ver.minor + 1}.0")
            if min_upper is None or upper_ver <= min_upper:
                min_upper = upper_ver
                min_upper_strict = True

    # 1. Exact pins must satisfy the entire combined SpecifierSet
    for pin in exact_pins:
        if pin not in combined:
            return False, f"Pinned version {pin} conflicts with range requirements"

    # 2. Check bounds contradiction
    if max_lower is not None and min_upper is not None:
        if max_lower > min_upper:
            return (
                False,
                f"Lower bound {max_lower} conflicts with upper bound {min_upper}",
            )
        if max_lower == min_upper and (max_lower_strict or min_upper_strict):
            return False, f"Strict bounds conflict at version {max_lower}"
        if max_lower == min_upper and (max_lower not in combined):
            return False, f"Bounds meet at excluded version {max_lower}"

    return True, "Specifiers are compatible or have overlapping ranges"


def _is_valid_version(v_str: str) -> bool:
    try:
        Version(v_str)
        return True
    except InvalidVersion:
        return False


def _parse_requirement_line(
    raw_line: str,
    file_rel: str,
    line_no: int,
    scope: DependencyScope,
) -> tuple[DiscoveredDependency | None, str | None]:
    """Parse a single requirements.txt line into a DiscoveredDependency or commented pin."""
    line = raw_line.strip()
    if not line:
        return None, None

    # Check for commented-out pin
    comment_match = COMMENTED_PIN_REGEX.match(line)
    if comment_match:
        pkg_name = comment_match.group(1).strip()
        constraint = comment_match.group(2).strip()
        return None, f"{pkg_name}{constraint} (in {file_rel}:{line_no})"

    # Skip comments
    if line.startswith("#"):
        return None, None

    # Skip flags and options
    ignored_prefixes = (
        "--index-url",
        "--extra-index-url",
        "-f ",
        "--find-links",
        "--trusted-host",
    )
    if any(line.startswith(opt) for opt in ignored_prefixes):
        return None, None

    # Strip inline hashes
    clean = re.sub(r"--hash=\S+", "", line).strip()

    # Check for non-PyPI sources (git, urls, editable, local paths)
    if clean.startswith("-e ") or clean.startswith("--editable "):
        editable_target = clean.split(maxsplit=1)[1].strip()
        name = Path(editable_target).name or editable_target
        rec = DeclaredSpecifierRecord(
            file=file_rel,
            line=line_no,
            specifier=editable_target,
            scope=scope,
        )
        return DiscoveredDependency(
            name=name,
            canonical_name=canonicalize_name(name),
            is_pypi=False,
            url_or_vcs=editable_target,
            declared_specifiers=[rec],
        ), None

    if any(clean.startswith(proto) for proto in ("git+", "http://", "https://", "svn+", "hg+")):
        name_guess = clean.split("/")[-1].split("@")[0].split(".git")[0]
        rec = DeclaredSpecifierRecord(
            file=file_rel,
            line=line_no,
            specifier=clean,
            scope=scope,
        )
        return DiscoveredDependency(
            name=name_guess,
            canonical_name=canonicalize_name(name_guess),
            is_pypi=False,
            url_or_vcs=clean,
            declared_specifiers=[rec],
        ), None

    try:
        req = Requirement(clean)
        return DiscoveredDependency(
            name=req.name,
            canonical_name=canonicalize_name(req.name),
            is_pypi=True,
            declared_specifiers=[
                DeclaredSpecifierRecord(
                    file=file_rel,
                    line=line_no,
                    specifier=str(req.specifier) if req.specifier else "",
                    scope=scope,
                )
            ],
        ), None
    except InvalidRequirement:
        return None, None


def parse_requirements_file(
    path: Path,
    root: Path,
    scope: DependencyScope = DependencyScope.RUNTIME,
    visited: set[Path] | None = None,
) -> tuple[list[DiscoveredDependency], list[str]]:
    """Recursively parse a requirements.txt file following -r includes."""
    if visited is None:
        visited = set()

    real_path = path.resolve()
    if real_path in visited or not path.exists():
        return [], []
    visited.add(real_path)

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return [], []

    file_rel = str(path.relative_to(root)) if path.is_relative_to(root) else path.name
    deps: list[DiscoveredDependency] = []
    commented_pins: list[str] = []

    for idx, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        # Handle -r / --requirement includes
        if line.startswith(("-r ", "--requirement ")):
            inc_target = line.split(maxsplit=1)[1].strip()
            inc_path = path.parent / inc_target
            sub_deps, sub_pins = parse_requirements_file(inc_path, root, scope, visited)
            deps.extend(sub_deps)
            commented_pins.extend(sub_pins)
            continue

        dep, pin = _parse_requirement_line(raw_line, file_rel, idx, scope)
        if dep:
            deps.append(dep)
        if pin:
            commented_pins.append(pin)

    return deps, commented_pins


def parse_pyproject_toml(path: Path, root: Path) -> list[DiscoveredDependency]:
    """Parse dependencies from pyproject.toml."""
    try:
        content = path.read_text(encoding="utf-8")
        data = tomllib.loads(content)
    except Exception:
        return []

    file_rel = str(path.relative_to(root)) if path.is_relative_to(root) else path.name
    deps: list[DiscoveredDependency] = []

    # 1. PEP 621 [project.dependencies]
    proj = data.get("project", {})
    if isinstance(proj, dict):
        for raw_req in proj.get("dependencies", []):
            if isinstance(raw_req, str):
                try:
                    req = Requirement(raw_req)
                    deps.append(
                        DiscoveredDependency(
                            name=req.name,
                            canonical_name=canonicalize_name(req.name),
                            declared_specifiers=[
                                DeclaredSpecifierRecord(
                                    file=file_rel,
                                    line=1,
                                    specifier=str(req.specifier) if req.specifier else "",
                                    scope=DependencyScope.RUNTIME,
                                )
                            ],
                        )
                    )
                except InvalidRequirement:
                    pass

        # [project.optional-dependencies]
        opt_deps = proj.get("optional-dependencies", {})
        if isinstance(opt_deps, dict):
            for group_name, req_list in opt_deps.items():
                scope = (
                    DependencyScope.DEV
                    if any(k in group_name.lower() for k in ("dev", "test"))
                    else DependencyScope.OPTIONAL
                )
                if isinstance(req_list, list):
                    for raw_req in req_list:
                        if isinstance(raw_req, str):
                            try:
                                req = Requirement(raw_req)
                                deps.append(
                                    DiscoveredDependency(
                                        name=req.name,
                                        canonical_name=canonicalize_name(req.name),
                                        declared_specifiers=[
                                            DeclaredSpecifierRecord(
                                                file=file_rel,
                                                line=1,
                                                specifier=(
                                                    str(req.specifier) if req.specifier else ""
                                                ),
                                                scope=scope,
                                            )
                                        ],
                                    )
                                )
                            except InvalidRequirement:
                                pass

    # 2. PEP 735 [dependency-groups]
    dep_groups = data.get("dependency-groups", {})
    if isinstance(dep_groups, dict):
        for group_name, req_list in dep_groups.items():
            scope = DependencyScope.TEST if "test" in group_name.lower() else DependencyScope.DEV
            if isinstance(req_list, list):
                for raw_req in req_list:
                    if isinstance(raw_req, str):
                        try:
                            req = Requirement(raw_req)
                            deps.append(
                                DiscoveredDependency(
                                    name=req.name,
                                    canonical_name=canonicalize_name(req.name),
                                    declared_specifiers=[
                                        DeclaredSpecifierRecord(
                                            file=file_rel,
                                            line=1,
                                            specifier=str(req.specifier) if req.specifier else "",
                                            scope=scope,
                                        )
                                    ],
                                )
                            )
                        except InvalidRequirement:
                            pass

    # 3. Poetry [tool.poetry.dependencies]
    tool_poetry = data.get("tool", {}).get("poetry", {})
    if isinstance(tool_poetry, dict):
        poetry_deps = tool_poetry.get("dependencies", {})
        if isinstance(poetry_deps, dict):
            for pkg_name, constraint in poetry_deps.items():
                if pkg_name.lower() == "python":
                    continue
                spec_str = constraint if isinstance(constraint, str) else str(constraint)
                deps.append(
                    DiscoveredDependency(
                        name=pkg_name,
                        canonical_name=canonicalize_name(pkg_name),
                        declared_specifiers=[
                            DeclaredSpecifierRecord(
                                file=file_rel,
                                line=1,
                                specifier=spec_str,
                                scope=DependencyScope.RUNTIME,
                            )
                        ],
                    )
                )

    return deps


def parse_setup_cfg(path: Path, root: Path) -> list[DiscoveredDependency]:
    """Parse dependencies from setup.cfg."""
    config = configparser.ConfigParser()
    try:
        config.read(str(path), encoding="utf-8")
    except Exception:
        return []

    file_rel = str(path.relative_to(root)) if path.is_relative_to(root) else path.name
    deps: list[DiscoveredDependency] = []

    if config.has_section("options"):
        # install_requires
        if config.has_option("options", "install_requires"):
            raw_reqs = config.get("options", "install_requires").strip().splitlines()
            for idx, raw_req in enumerate(raw_reqs, start=1):
                clean = raw_req.strip()
                if not clean or clean.startswith("#"):
                    continue
                try:
                    req = Requirement(clean)
                    deps.append(
                        DiscoveredDependency(
                            name=req.name,
                            canonical_name=canonicalize_name(req.name),
                            declared_specifiers=[
                                DeclaredSpecifierRecord(
                                    file=file_rel,
                                    line=idx,
                                    specifier=str(req.specifier) if req.specifier else "",
                                    scope=DependencyScope.RUNTIME,
                                )
                            ],
                        )
                    )
                except InvalidRequirement:
                    pass

    return deps


def parse_setup_py(path: Path, root: Path) -> tuple[list[DiscoveredDependency], list[str]]:
    """Parse literal install_requires in setup.py via AST analysis."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except Exception:
        return [], ["Failed to parse setup.py with AST"]

    file_rel = str(path.relative_to(root)) if path.is_relative_to(root) else path.name
    deps: list[DiscoveredDependency] = []
    limitations: list[str] = []

    class SetupVisitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            # Check for setup(...)
            func_name = ""
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                func_name = node.func.attr

            if func_name in ("setup", "setup_package"):
                for kw in node.keywords:
                    if kw.arg == "install_requires":
                        if isinstance(kw.value, (ast.List, ast.Tuple)):
                            for elt in kw.value.elts:
                                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                    try:
                                        req = Requirement(elt.value)
                                        deps.append(
                                            DiscoveredDependency(
                                                name=req.name,
                                                canonical_name=canonicalize_name(req.name),
                                                declared_specifiers=[
                                                    DeclaredSpecifierRecord(
                                                        file=file_rel,
                                                        line=elt.lineno,
                                                        specifier=(
                                                            str(req.specifier)
                                                            if req.specifier
                                                            else ""
                                                        ),
                                                        scope=DependencyScope.RUNTIME,
                                                    )
                                                ],
                                            )
                                        )
                                    except InvalidRequirement:
                                        pass
                        else:
                            limitations.append(
                                "Dynamic install_requires in setup.py cannot be "
                                "safely extracted via static AST"
                            )
            self.generic_visit(node)

    SetupVisitor().visit(tree)
    return deps, limitations


def parse_lockfiles(target_dir: Path) -> dict[str, str]:
    """Parse resolved versions from uv.lock or poetry.lock if present."""
    resolved_map: dict[str, str] = {}

    uv_lock = target_dir / "uv.lock"
    if uv_lock.exists():
        try:
            data = tomllib.loads(uv_lock.read_text(encoding="utf-8"))
            for pkg in data.get("package", []):
                name = pkg.get("name")
                version = pkg.get("version")
                if name and version:
                    resolved_map[canonicalize_name(name)] = version
        except Exception:
            pass

    poetry_lock = target_dir / "poetry.lock"
    if poetry_lock.exists() and not resolved_map:
        try:
            data = tomllib.loads(poetry_lock.read_text(encoding="utf-8"))
            for pkg in data.get("package", []):
                name = pkg.get("name")
                version = pkg.get("version")
                if name and version:
                    resolved_map[canonicalize_name(name)] = version
        except Exception:
            pass

    return resolved_map


def discover_dependencies(target_dir: Path | str) -> DiscoveryResult:
    """Discover and merge all direct dependencies declared across target manifests."""
    root = Path(target_dir).resolve()
    raw_deps: list[DiscoveredDependency] = []
    commented_pins: list[str] = []
    limitations: list[str] = []
    primary_manifest: str | None = None

    # 1. pyproject.toml
    pyproject_file = root / "pyproject.toml"
    if pyproject_file.exists():
        primary_manifest = primary_manifest or "pyproject.toml"
        raw_deps.extend(parse_pyproject_toml(pyproject_file, root))

    # 2. requirements*.txt
    for req_file in sorted(root.glob("requirements*.txt")):
        primary_manifest = primary_manifest or req_file.name
        scope = (
            DependencyScope.DEV
            if any(k in req_file.name.lower() for k in ("dev", "test"))
            else DependencyScope.RUNTIME
        )
        sub_deps, sub_pins = parse_requirements_file(req_file, root, scope=scope)
        raw_deps.extend(sub_deps)
        commented_pins.extend(sub_pins)

    # 3. setup.cfg
    setup_cfg = root / "setup.cfg"
    if setup_cfg.exists():
        primary_manifest = primary_manifest or "setup.cfg"
        raw_deps.extend(parse_setup_cfg(setup_cfg, root))

    # 4. setup.py
    setup_py = root / "setup.py"
    if setup_py.exists():
        primary_manifest = primary_manifest or "setup.py"
        py_deps, py_limits = parse_setup_py(setup_py, root)
        raw_deps.extend(py_deps)
        limitations.extend(py_limits)

    # 5. Lockfiles
    lock_resolved = parse_lockfiles(root)

    # Merge by canonical name
    merged: dict[str, DiscoveredDependency] = {}
    for dep in raw_deps:
        cname = dep.canonical_name
        if cname not in merged:
            merged[cname] = dep
        else:
            existing = merged[cname]
            combined_records = list(existing.declared_specifiers) + list(dep.declared_specifiers)
            merged[cname] = DiscoveredDependency(
                name=existing.name,
                canonical_name=cname,
                is_pypi=existing.is_pypi and dep.is_pypi,
                url_or_vcs=existing.url_or_vcs or dep.url_or_vcs,
                declared_specifiers=combined_records,
                resolved_version=existing.resolved_version or dep.resolved_version,
            )

    # Attach lockfile versions and compute manifest divergence
    final_deps: list[DiscoveredDependency] = []
    for cname, dep in merged.items():
        resolved = lock_resolved.get(cname) or dep.resolved_version

        # Compute divergence across files
        file_specs: dict[str, str] = {}
        for rec in dep.declared_specifiers:
            file_specs[rec.file] = rec.specifier

        divergence: ManifestDivergence | None = None
        if len(file_specs) > 1:
            specs = list(file_specs.values())
            files = list(file_specs.keys())
            # Check if all specifier strings are identical
            if len(set(specs)) > 1:
                compat, detail = _check_specifiers_compatible(specs)
                divergence = ManifestDivergence(
                    files=files,
                    specifiers=specs,
                    compatible=compat,
                    detail=detail,
                )

        final_deps.append(
            DiscoveredDependency(
                name=dep.name,
                canonical_name=cname,
                is_pypi=dep.is_pypi,
                url_or_vcs=dep.url_or_vcs,
                declared_specifiers=dep.declared_specifiers,
                resolved_version=resolved,
                divergence=divergence,
            )
        )

    # Sort alphabetically by canonical name
    final_deps.sort(key=lambda d: d.canonical_name)

    return DiscoveryResult(
        target_path=str(root),
        dependencies=final_deps,
        primary_manifest=primary_manifest,
        commented_out_pins=commented_pins,
        limitations=limitations,
    )
