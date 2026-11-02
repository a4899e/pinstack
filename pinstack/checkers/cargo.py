"""Cargo checker: enforces checksum presence in Cargo.lock for registry packages."""

from __future__ import annotations

import fnmatch
import os
import re
from typing import Optional

from pinstack.core import Checker, Finding, FileIndex

# Sections in Cargo.toml that list dependencies
_DEP_SECTION_NAMES = frozenset(
    [
        "dependencies",
        "dev-dependencies",
        "build-dependencies",
    ]
)


def _parse_cargo_toml_deps(path: str) -> list[str]:
    """Return list of dependency names from Cargo.toml."""
    deps: list[str] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return deps

    in_dep_section = False
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # Section header
        if line.startswith("["):
            # Strip table-array brackets and inline-table notation
            section = line.strip("[]").strip()
            parts = [p.strip().strip("\"'") for p in section.split(".")]
            # [dependencies], [workspace.dependencies], [target.x.dependencies]
            in_dep_section = parts[-1] in _DEP_SECTION_NAMES
            # [dependencies.serde]: a single dependency written as a table
            if len(parts) >= 2 and parts[-2] in _DEP_SECTION_NAMES:
                deps.append(parts[-1])
            continue
        if not in_dep_section:
            continue
        # Dependency line: "name = ...", "name = { ... }" or "name.workspace = true"
        m = re.match(r"^([A-Za-z0-9_\-]+)(?:\.[A-Za-z0-9_\-]+)*\s*=", line)
        if m:
            deps.append(m.group(1))

    return deps


def _parse_cargo_lock_names(lines: list[str]) -> set[str]:
    """Return set of package names from already-read Cargo.lock lines."""
    names: set[str] = set()
    for raw_line in lines:
        line = raw_line.strip()
        if line.startswith("name = "):
            names.add(line[len("name = ") :].strip().strip('"'))
    return names


_WORKSPACE_HEADER_RE = re.compile(r"^[ \t]*\[workspace\][ \t]*$", re.M)
_TABLE_HEADER_RE = re.compile(r"^[ \t]*\[", re.M)
_PATH_DEP_RE = re.compile(r"\bpath\s*=\s*\"([^\"]+)\"")


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _workspace_array(content: str, key: str) -> list[str]:
    """Return the strings of ``key = [...]`` in a Cargo.toml's [workspace] table."""
    header = _WORKSPACE_HEADER_RE.search(content)
    if not header:
        return []
    next_header = _TABLE_HEADER_RE.search(content, header.end())
    table = content[header.end() : next_header.start() if next_header else None]
    m = re.search(r"^\s*" + key + r"\s*=\s*\[(.*?)\]", table, re.M | re.S)
    return re.findall(r"\"([^\"]*)\"", m.group(1)) if m else []


def _glob_matches(rel_path: str, pattern: str) -> bool:
    """Match a path against a Cargo member glob, where ``*`` stays within a segment."""
    path_parts = rel_path.split("/")
    pattern_parts = pattern.strip("/").split("/")
    if len(path_parts) != len(pattern_parts):
        return False
    return all(fnmatch.fnmatchcase(p, q) for p, q in zip(path_parts, pattern_parts))


def _is_workspace_member(crate_dir: str, ws_dir: str, ws_content: str) -> bool:
    """Return True if ``crate_dir`` belongs to the workspace rooted at ``ws_dir``.

    A crate is a member when a ``members`` glob or a path dependency of the
    root manifest names its directory, and no ``exclude`` entry covers it.
    """
    rel = os.path.relpath(crate_dir, ws_dir).replace(os.sep, "/")
    for excluded in _workspace_array(ws_content, "exclude"):
        excluded = excluded.strip("/")
        if rel == excluded or rel.startswith(excluded + "/"):
            return False
    for member in _workspace_array(ws_content, "members"):
        if _glob_matches(rel, member):
            return True
    for dep_path in _PATH_DEP_RE.findall(ws_content):
        if os.path.normpath(os.path.join(ws_dir, dep_path)) == os.path.normpath(
            crate_dir
        ):
            return True
    return False


def _workspace_root(crate_dir: str, root: str) -> Optional[str]:
    """Return the workspace root directory that ``crate_dir`` is a member of.

    Cargo looks upward for the nearest Cargo.toml with a [workspace] table.
    The search stops at the scan root. Returns None for a standalone crate,
    including one inside a workspace directory that does not list it.
    """
    root = os.path.abspath(root)
    current = os.path.abspath(crate_dir)
    while current != root:
        parent = os.path.dirname(current)
        if parent == current or os.path.commonpath([root, parent]) != root:
            return None
        current = parent
        content = _read_text(os.path.join(current, "Cargo.toml"))
        if content is not None and _WORKSPACE_HEADER_RE.search(content):
            if _is_workspace_member(crate_dir, current, content):
                return current
            return None
    return None


class CargoChecker(Checker):
    name = "cargo"
    description = (
        "Checks Cargo.lock checksums, staleness, and that every crate with"
        " dependencies has one"
    )
    patterns: list[str] = ["Cargo.lock", "Cargo.toml"]

    def _check_lock_present(self, dir_path: str, root: str) -> Optional[Finding]:
        """Check a Cargo.toml that has no Cargo.lock beside it.

        A workspace member is resolved by the workspace root's Cargo.lock, so
        its dependencies are cross-referenced against that lock. Any other
        crate with dependencies needs a Cargo.lock of its own.
        """
        toml_path = os.path.join(dir_path, "Cargo.toml")
        rel_toml = os.path.relpath(toml_path, root)
        deps = _parse_cargo_toml_deps(toml_path)
        if not deps:
            return None

        own = _read_text(toml_path) or ""
        ws_dir = (
            None
            if _WORKSPACE_HEADER_RE.search(own)
            else _workspace_root(dir_path, root)
        )
        lock_path = os.path.join(ws_dir, "Cargo.lock") if ws_dir else None
        if lock_path is None or not os.path.isfile(lock_path):
            return Finding(
                checker=self.name,
                path=rel_toml,
                line=0,
                message=(
                    "Cargo.toml has dependencies but no Cargo.lock here or in its"
                    " workspace root; run 'cargo generate-lockfile' and commit Cargo.lock"
                ),
                integrity=True,
            )

        with open(lock_path, "r", encoding="utf-8", errors="replace") as fh:
            lock_names = _parse_cargo_lock_names(fh.readlines())
        missing = sorted({dep for dep in deps if dep not in lock_names})
        if not missing:
            return None
        return Finding(
            checker=self.name,
            path=rel_toml,
            line=0,
            message="{} is stale: missing {} ({})".format(
                os.path.relpath(lock_path, root),
                "{} dependency".format(len(missing))
                if len(missing) == 1
                else "{} dependencies".format(len(missing)),
                ", ".join(missing),
            ),
            integrity=True,
        )

    def check(self, index: FileIndex, root: str) -> list[Finding]:
        findings: list[Finding] = []

        for dir_path in sorted(index.keys()):
            files = index[dir_path]

            if "Cargo.lock" not in files:
                if "Cargo.toml" in files:
                    finding = self._check_lock_present(dir_path, root)
                    if finding:
                        findings.append(finding)
                continue

            lock_path = os.path.join(dir_path, "Cargo.lock")
            rel_lock = os.path.relpath(lock_path, root)

            try:
                with open(lock_path, "r", encoding="utf-8", errors="replace") as fh:
                    lines = fh.readlines()
            except OSError:
                continue

            # Parse [[package]] blocks for checksum checks.
            # Only registry packages (source = "registry+...") require checksums.
            # Git packages (source = "git+...") legitimately have no checksum.
            in_package = False
            pkg_name: Optional[str] = None
            pkg_line = 0
            is_registry_source = False
            has_checksum = False

            def flush_package(findings_list: list[Finding], rp: str) -> None:
                if (
                    in_package
                    and is_registry_source
                    and not has_checksum
                    and pkg_name is not None
                ):
                    findings_list.append(
                        Finding(
                            checker="cargo",
                            path=rp,
                            line=pkg_line,
                            message="package '{}' from registry is missing checksum".format(
                                pkg_name
                            ),
                            integrity=True,
                        )
                    )

            for lineno, raw_line in enumerate(lines, start=1):
                line = raw_line.strip()

                if line == "[[package]]":
                    flush_package(findings, rel_lock)
                    in_package = True
                    pkg_name = None
                    pkg_line = lineno
                    is_registry_source = False
                    has_checksum = False
                    continue

                if not in_package:
                    continue

                if line.startswith("name = "):
                    pkg_name = line[len("name = ") :].strip().strip('"')
                elif line.startswith("source = "):
                    source_val = line[len("source = ") :].strip().strip('"')
                    is_registry_source = source_val.startswith("registry+")
                elif line.startswith("checksum = "):
                    has_checksum = True

            # Flush last package
            flush_package(findings, rel_lock)

            # Cross-reference: every Cargo.toml dep must appear in Cargo.lock
            if "Cargo.toml" in files:
                toml_path = os.path.join(dir_path, "Cargo.toml")
                toml_deps = _parse_cargo_toml_deps(toml_path)
                lock_names = _parse_cargo_lock_names(lines)

                missing = [dep for dep in toml_deps if dep not in lock_names]
                if missing:
                    findings.append(
                        Finding(
                            checker=self.name,
                            path=rel_lock,
                            line=0,
                            message="Cargo.lock is stale: missing {} ({})".format(
                                "{} dependency".format(len(missing))
                                if len(missing) == 1
                                else "{} dependencies".format(len(missing)),
                                ", ".join(sorted(missing)),
                            ),
                            integrity=True,
                        )
                    )

        return findings
