"""AST-based dependency usage mapping and blast radius analyzer."""

from __future__ import annotations

import ast
from pathlib import Path

from packaging.utils import canonicalize_name

from exitkit.discover.manifests import discover_dependencies
from exitkit.usage.models import (
    BlastRadius,
    CriticalPathLevel,
    DependencyUsage,
    UsageKind,
    UsageReport,
    UsageSite,
)
from exitkit.usage.resolver import (
    resolve_dist_to_modules,
    resolve_module_to_dist,
)
from exitkit.util.fingerprint import tree_fingerprint

IGNORED_DIRS = frozenset(
    {
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        "env",
        ".tox",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".exitkit",
        ".targets",
        "build",
        "dist",
        "node_modules",
        ".eggs",
    }
)

CORE_FILE_PATTERNS = frozenset(
    {
        "main.py",
        "__main__.py",
        "cli.py",
        "app.py",
        "core.py",
        "config.py",
        "settings.py",
        "__init__.py",
    }
)


class FileUsageVisitor(ast.NodeVisitor):
    """AST visitor extracting dependency imports, calls, attribute accesses, and annotations."""

    def __init__(
        self,
        file_rel: str,
        source_lines: list[str],
        declared_dists: set[str],
        local_pkg_names: set[str] | None = None,
    ) -> None:
        self.file_rel = file_rel
        self.source_lines = source_lines
        self.declared_dists = declared_dists
        self.local_pkg_names = local_pkg_names or set()

        # Local scope table: local_name -> (dist_name, qualified_symbol)
        self.scope_map: dict[str, tuple[str, str]] = {}
        # Instance variable table: var_name -> (dist_name, qualified_type)
        self.instance_map: dict[str, tuple[str, str]] = {}
        # Nodes already processed to prevent duplicate recording
        self.handled_nodes: set[ast.AST] = set()
        # Collected usage sites
        self.sites: list[UsageSite] = []

    def _get_snippet(self, lineno: int) -> str:
        if 1 <= lineno <= len(self.source_lines):
            return self.source_lines[lineno - 1].strip()
        return ""

    def _add_site(
        self,
        node: ast.AST,
        kind: UsageKind,
        dist: str,
        symbol: str,
    ) -> None:
        lineno = getattr(node, "lineno", 1)
        col = getattr(node, "col_offset", 0)
        snippet = self._get_snippet(lineno)
        self.sites.append(
            UsageSite(
                file=self.file_rel,
                line=lineno,
                col=col,
                symbol=symbol,
                kind=kind,
                snippet=snippet,
            )
        )

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            top_module = alias.name.split(".")[0]
            if canonicalize_name(top_module) in self.local_pkg_names:
                continue
            dist = resolve_module_to_dist(top_module, self.declared_dists)
            if not dist:
                continue

            local_name = alias.asname or alias.name
            qualified = alias.name
            self.scope_map[local_name] = (dist, qualified)
            self._add_site(node, UsageKind.IMPORT, dist, qualified)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        # Ignore relative imports within the project (e.g. from .config import ...)
        if node.level and node.level > 0:
            self.generic_visit(node)
            return

        if not node.module:
            self.generic_visit(node)
            return

        top_module = node.module.split(".")[0]
        if canonicalize_name(top_module) in self.local_pkg_names:
            self.generic_visit(node)
            return

        dist = resolve_module_to_dist(top_module, self.declared_dists)
        if not dist:
            self.generic_visit(node)
            return

        for alias in node.names:
            if alias.name == "*":
                self.scope_map["*"] = (dist, node.module)
                self._add_site(node, UsageKind.IMPORT, dist, f"{node.module}.*")
            else:
                local_name = alias.asname or alias.name
                qualified = f"{node.module}.{alias.name}"
                self.scope_map[local_name] = (dist, qualified)
                self._add_site(node, UsageKind.IMPORT, dist, qualified)
        self.generic_visit(node)

    def _get_chain(self, node: ast.AST) -> tuple[str, list[str]] | None:
        """Extract a base name and sequence of attribute accesses."""
        if isinstance(node, ast.Attribute):
            sub = self._get_chain(node.value)
            if sub:
                return (sub[0], sub[1] + [node.attr])
        elif isinstance(node, ast.Name):
            return (node.id, [])
        return None

    def _mark_handled_chain(self, node: ast.AST) -> None:
        curr: ast.AST | None = node
        while isinstance(curr, ast.Attribute):
            self.handled_nodes.add(curr)
            curr = curr.value
        if curr is not None:
            self.handled_nodes.add(curr)

    def _resolve_chain(self, chain_info: tuple[str, list[str]]) -> tuple[str, str] | None:
        """Resolve an attribute chain against scope_map and instance_map."""
        base_name, attrs = chain_info
        if base_name in self.scope_map:
            dist, root_sym = self.scope_map[base_name]
            sym = f"{root_sym}.{'.'.join(attrs)}" if attrs else root_sym
            return (dist, sym)

        if base_name in self.instance_map:
            dist, type_sym = self.instance_map[base_name]
            sym = f"{type_sym}.{'.'.join(attrs)}" if attrs else type_sym
            return (dist, sym)

        for i in range(1, len(attrs) + 1):
            prefix = f"{base_name}.{'.'.join(attrs[:i])}"
            if prefix in self.instance_map:
                dist, type_sym = self.instance_map[prefix]
                remaining = attrs[i:]
                sym = f"{type_sym}.{'.'.join(remaining)}" if remaining else type_sym
                return (dist, sym)

        return None

    def _resolve_callable(self, func_node: ast.AST) -> tuple[str, str] | None:
        """Resolve a function or method invocation to (distribution, symbol)."""
        if isinstance(func_node, ast.Name):
            if func_node.id in self.scope_map:
                return self.scope_map[func_node.id]
            if func_node.id in self.instance_map:
                return self.instance_map[func_node.id]

        if isinstance(func_node, ast.Attribute):
            chain_info = self._get_chain(func_node)
            if chain_info:
                return self._resolve_chain(chain_info)
        return None

    def visit_Assign(self, node: ast.Assign) -> None:
        # Check if right-hand side is a call returning a known instance
        if isinstance(node.value, ast.Call):
            resolved = self._resolve_callable(node.value.func)
            if resolved:
                dist, sym = resolved
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self.instance_map[target.id] = (dist, sym)
                    elif isinstance(target, ast.Attribute):
                        chain_info = self._get_chain(target)
                        if chain_info:
                            full_target = f"{chain_info[0]}.{'.'.join(chain_info[1])}"
                            self.instance_map[full_target] = (dist, sym)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        resolved = self._resolve_callable(node.func)
        if resolved:
            dist, sym = resolved
            self._add_site(node, UsageKind.CALL, dist, sym)
            self._mark_handled_chain(node.func)

        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node in self.handled_nodes:
            self.generic_visit(node)
            return

        chain_info = self._get_chain(node)
        if chain_info:
            resolved = self._resolve_chain(chain_info)
            if resolved:
                dist, sym = resolved
                self._add_site(node, UsageKind.ATTRIBUTE_ACCESS, dist, sym)
                self._mark_handled_chain(node)

        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        # Detect base classes deriving from dependency classes (e.g. class Site(BaseModel):)
        for base in node.bases:
            if isinstance(base, ast.Name) and base.id in self.scope_map:
                dist, sym = self.scope_map[base.id]
                self._add_site(base, UsageKind.ATTRIBUTE_ACCESS, dist, sym)
                self.handled_nodes.add(base)
            elif isinstance(base, ast.Attribute):
                chain_info = self._get_chain(base)
                if chain_info and chain_info[0] in self.scope_map:
                    dist, root_sym = self.scope_map[chain_info[0]]
                    sym = f"{root_sym}.{'.'.join(chain_info[1])}" if chain_info[1] else root_sym
                    self._add_site(base, UsageKind.ATTRIBUTE_ACCESS, dist, sym)
                    self._mark_handled_chain(base)

        for dec in node.decorator_list:
            func_target = dec.func if isinstance(dec, ast.Call) else dec
            resolved = self._resolve_callable(func_target)
            if resolved:
                dist, sym = resolved
                kind = UsageKind.CALL if isinstance(dec, ast.Call) else UsageKind.ATTRIBUTE_ACCESS
                self._add_site(dec, kind, dist, sym)
                self._mark_handled_chain(func_target)

        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # Detect decorators on functions (e.g. @click.command(), @validator("name"))
        for dec in node.decorator_list:
            func_target = dec.func if isinstance(dec, ast.Call) else dec
            resolved = self._resolve_callable(func_target)
            if resolved:
                dist, sym = resolved
                kind = UsageKind.CALL if isinstance(dec, ast.Call) else UsageKind.ATTRIBUTE_ACCESS
                self._add_site(dec, kind, dist, sym)
                self._mark_handled_chain(func_target)

        # Detect type annotations in arguments and return type
        for arg in node.args.args:
            if arg.annotation:
                self._check_annotation(arg.annotation)
        if node.returns:
            self._check_annotation(node.returns)

        self.generic_visit(node)

    def _check_annotation(self, anno_node: ast.AST) -> None:
        """Record usage of dependency symbols inside type annotations."""
        if isinstance(anno_node, ast.Name) and anno_node.id in self.scope_map:
            dist, sym = self.scope_map[anno_node.id]
            self._add_site(anno_node, UsageKind.TYPE_ANNOTATION, dist, sym)
            self.handled_nodes.add(anno_node)
        elif isinstance(anno_node, ast.Attribute):
            chain_info = self._get_chain(anno_node)
            if chain_info and chain_info[0] in self.scope_map:
                dist, root_sym = self.scope_map[chain_info[0]]
                sym = f"{root_sym}.{'.'.join(chain_info[1])}" if chain_info[1] else root_sym
                self._add_site(anno_node, UsageKind.TYPE_ANNOTATION, dist, sym)
                self._mark_handled_chain(anno_node)


def compute_blast_radius(
    files_touched: list[str],
    total_source_files: int,
    sites: list[UsageSite],
) -> BlastRadius:
    """Calculate blast radius and critical-path severity heuristic for a dependency."""
    files_touched_count = len(files_touched)
    total_source_files = max(1, total_source_files)
    share = round(files_touched_count / total_source_files, 4)
    total_calls = sum(1 for s in sites if s.kind == UsageKind.CALL)

    reasons: list[str] = []
    reasons.append(
        f"Touches {files_touched_count} of {total_source_files} source files ({share:.1%})"
    )
    if total_calls:
        reasons.append(f"Total call sites: {total_calls}")

    # Check for core/entrypoint files
    core_files: list[str] = []
    monkeypatch_files: list[str] = []
    for f in files_touched:
        fname = Path(f).name
        if fname in CORE_FILE_PATTERNS:
            core_files.append(f)
        if "patch" in fname.lower() or "hook" in fname.lower():
            monkeypatch_files.append(f)

    if core_files:
        core_str = ", ".join(sorted(core_files))
        reasons.append(f"Present in core/entrypoint module(s): {core_str}")
    if monkeypatch_files:
        patch_str = ", ".join(sorted(monkeypatch_files))
        reasons.append(f"Involved in patching/hook module(s): {patch_str}")

    # Assign CriticalPathLevel
    if share >= 0.50 or monkeypatch_files or (share >= 0.25 and core_files):
        level = CriticalPathLevel.CRITICAL
    elif share >= 0.25 or core_files or total_calls >= 8:
        level = CriticalPathLevel.HIGH
    elif files_touched_count >= 2 or total_calls >= 3:
        level = CriticalPathLevel.MEDIUM
    else:
        level = CriticalPathLevel.LOW

    return BlastRadius(
        files_touched_count=files_touched_count,
        total_source_files=total_source_files,
        files_touched_share=share,
        total_call_sites=total_calls,
        critical_path_level=level,
        critical_path_reasons=reasons,
    )


def map_repository_usage(
    repo_path: Path | str,
    target_dep: str | None = None,
) -> UsageReport:
    """Analyze all Python source files in a repository and map dependency usage."""
    root = Path(repo_path).resolve()
    if not root.exists():
        raise FileNotFoundError(f"Target repository path does not exist: {root}")

    # Invariant: Record tree fingerprint before and after (Rule 5)
    fp_before = tree_fingerprint(root)

    # 1. Discover declared dependencies
    try:
        disc = discover_dependencies(root)
        declared_dists = {d.canonical_name for d in disc.dependencies}
    except Exception:
        declared_dists = set()

    normalized_filter = canonicalize_name(target_dep) if target_dep else None

    # Detect local repository packages to exclude them from third-party dependencies
    local_pkg_names: set[str] = set()
    pyproject_file = root / "pyproject.toml"
    if pyproject_file.exists():
        try:
            import tomllib

            pdata = tomllib.loads(pyproject_file.read_text(encoding="utf-8"))
            if "project" in pdata and "name" in pdata["project"]:
                local_pkg_names.add(canonicalize_name(pdata["project"]["name"]))
            if "tool" in pdata and "poetry" in pdata["tool"] and "name" in pdata["tool"]["poetry"]:
                local_pkg_names.add(canonicalize_name(pdata["tool"]["poetry"]["name"]))
        except Exception:
            pass

    for item in root.iterdir():
        if item.name.startswith(".") or item.name in IGNORED_DIRS:
            continue
        if item.is_dir() and (item / "__init__.py").exists():
            local_pkg_names.add(canonicalize_name(item.name))
        elif item.is_file() and item.suffix == ".py":
            local_pkg_names.add(canonicalize_name(item.stem))
    local_pkg_names.add(canonicalize_name(root.name))

    # 2. Collect all Python source files
    source_files: list[Path] = []
    for path in root.rglob("*.py"):
        # Skip ignored directories
        rel = path.relative_to(root)
        if any(part in IGNORED_DIRS or part.startswith(".") for part in rel.parts[:-1]):
            continue
        source_files.append(path)

    source_files.sort()
    rel_source_files = [str(p.relative_to(root)) for p in source_files]
    total_sources = len(rel_source_files)

    # 3. Parse and extract usage per file
    # Map dist -> list of UsageSite
    dist_sites: dict[str, list[UsageSite]] = {}

    for py_file in source_files:
        rel_path = str(py_file.relative_to(root))
        try:
            content = py_file.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(content, filename=rel_path)
        except Exception:
            # Skip unparseable files gracefully
            continue

        lines = content.splitlines()
        visitor = FileUsageVisitor(
            file_rel=rel_path,
            source_lines=lines,
            declared_dists=declared_dists,
            local_pkg_names=local_pkg_names,
        )
        visitor.visit(tree)

        for site in visitor.sites:
            # Map site symbol's distribution
            dist = resolve_module_to_dist(site.symbol.split(".")[0], declared_dists)
            if not dist:
                continue

            if dist in local_pkg_names:
                continue

            if normalized_filter and dist != normalized_filter:
                continue

            dist_sites.setdefault(dist, []).append(site)

    # 4. Build DependencyUsage records
    usages: dict[str, DependencyUsage] = {}

    # Include all declared dependencies if no filter, or only matching
    candidate_dists = (
        {normalized_filter}
        if normalized_filter
        else ((declared_dists | set(dist_sites.keys())) - local_pkg_names)
    )

    for dist in sorted(candidate_dists):
        if not dist:
            continue
        sites = dist_sites.get(dist, [])
        files_touched = sorted({s.file for s in sites})
        active_syms = {s.symbol for s in sites if s.kind != UsageKind.IMPORT}
        symbols_used = sorted(active_syms or {s.symbol for s in sites})
        top_modules = resolve_dist_to_modules(dist)

        blast = compute_blast_radius(
            files_touched=files_touched,
            total_source_files=total_sources,
            sites=sites,
        )

        usages[dist] = DependencyUsage(
            distribution_name=dist,
            top_level_modules=top_modules,
            files_touched=files_touched,
            symbols_used=symbols_used,
            sites=sites,
            blast_radius=blast,
        )

    # 5. Invariant check: target repo was untouched
    fp_after = tree_fingerprint(root)
    if fp_before != fp_after:
        raise RuntimeError(
            f"Target repository safety violation: tree fingerprint changed during usage analysis! "
            f"Before: {fp_before}, After: {fp_after}"
        )

    return UsageReport(
        target_path=str(root),
        total_source_files=total_sources,
        source_files=rel_source_files,
        usages=usages,
    )
