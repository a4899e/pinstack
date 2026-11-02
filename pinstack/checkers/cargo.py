"""Cargo checker: enforces checksum presence in Cargo.lock for registry packages."""

from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass, field
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


@dataclass
class _Dependency:
    key: str  # the name the manifest uses
    package: Optional[str] = None  # `package = "..."` rename
    inherits: bool = False  # `workspace = true`
    path: Optional[str] = None  # `path = "..."`


@dataclass
class _Manifest:
    """The parts of a Cargo.toml that decide what must be locked, and where."""

    has_workspace: bool = False
    members: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    # [dependencies], [dev-dependencies], [build-dependencies], target.*
    dependencies: list[_Dependency] = field(default_factory=list)
    # [workspace.dependencies], which members inherit with workspace = true
    workspace_dependencies: dict[str, _Dependency] = field(default_factory=dict)


_STRING_RE = re.compile(r"""\"((?:[^\"\\]|\\.)*)\"|'([^']*)'""")


def _strip_toml_comment(line: str) -> str:
    """Remove a ``#`` comment that is outside TOML strings."""
    quote = ""
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            if ch == "\\" and quote == '"':
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "#":
            return line[:i]
        i += 1
    return line


def _split_dotted(key: str) -> list[str]:
    """Split a TOML key on dots outside quotes, unquoting each part."""
    parts: list[str] = []
    for m in re.finditer(r"""\s*(?:"([^"]*)"|'([^']*)'|([^."'\s]+))\s*(?:\.|$)""", key):
        part = next((g for g in m.groups() if g is not None), None)
        if part is not None:
            parts.append(part)
    return parts


def _strings(value: str) -> list[str]:
    """Return every TOML string literal (either quote form) in ``value``."""
    return [double or single for double, single in _STRING_RE.findall(value)]


def _inline_field(value: str, name: str) -> Optional[str]:
    """Return the value of ``name = ...`` inside an inline table, if any."""
    m = re.search(
        r"\b" + name + r"""\s*=\s*(?:"([^"]*)"|'([^']*)'|(true|false))""", value
    )
    if not m:
        return None
    return next(g for g in m.groups() if g is not None)


def _apply_dependency_field(dep: _Dependency, name: str, value: str) -> None:
    if name == "package":
        dep.package = (_strings(value) or [None])[0]
    elif name == "workspace":
        dep.inherits = value.strip() == "true"
    elif name == "path":
        dep.path = (_strings(value) or [None])[0]


def _parse_manifest(path: str) -> _Manifest:
    """Read a Cargo.toml without a TOML library (pinstack has no dependencies).

    Handles comments, quoted and dotted keys, single- and double-quoted
    strings, multi-line arrays, inline tables, and dependency tables of
    their own (``[dependencies.serde]``).
    """
    manifest = _Manifest()
    content = _read_text(path)
    if content is None:
        return manifest

    section: list[str] = []
    table_dep: Optional[_Dependency] = None
    # Dotted keys (`serde1.package = ...`, `serde1.version = ...`) spread one
    # dependency over several lines of a section; collect them in one entry.
    dotted: dict[str, _Dependency] = {}
    pending_key: Optional[str] = None
    pending_value = ""
    for raw in content.splitlines():
        line = _strip_toml_comment(raw).strip()
        if pending_key is not None:
            pending_value += " " + line
            if pending_value.count("[") <= pending_value.count("]"):
                line, pending_key, pending_value = (
                    pending_key + " = " + pending_value,
                    None,
                    "",
                )
            else:
                continue
        if not line:
            continue

        # 1. Table headers.
        if line.startswith("["):
            table_dep = None
            dotted = {}
            section = _split_dotted(line.strip("[] "))
            if section == ["workspace"]:
                manifest.has_workspace = True
            # [dependencies.serde] and [target.x.dependencies.serde]
            if len(section) >= 2 and section[-2] in _DEP_SECTION_NAMES:
                table_dep = _Dependency(key=section[-1])
                if section[:-1] == ["workspace", "dependencies"]:
                    manifest.workspace_dependencies[table_dep.key] = table_dep
                else:
                    manifest.dependencies.append(table_dep)
            continue

        # 2. key = value; a multi-line array continues on later lines.
        key, sep, value = line.partition("=")
        if not sep:
            continue
        value = value.strip()
        if value.startswith("[") and value.count("[") > value.count("]"):
            pending_key, pending_value = key, value
            continue
        key_parts = _split_dotted(key)
        if not key_parts:
            continue

        if table_dep is not None:
            _apply_dependency_field(table_dep, key_parts[0], value)
            continue
        if section == ["workspace"]:
            if key_parts == ["members"]:
                manifest.members = _strings(value)
            elif key_parts == ["exclude"]:
                manifest.exclude = _strings(value)
            continue
        if not section or section[-1] not in _DEP_SECTION_NAMES:
            continue

        # 3. A dependency: name = "1", name = { ... }, or name.field = ...
        if len(key_parts) > 1:
            if key_parts[0] in dotted:
                _apply_dependency_field(dotted[key_parts[0]], key_parts[1], value)
                continue
            dep = _Dependency(key=key_parts[0])
            dotted[dep.key] = dep
            _apply_dependency_field(dep, key_parts[1], value)
        elif value.startswith("{"):
            dep = _Dependency(key=key_parts[0])
            dep.package = _inline_field(value, "package")
            dep.inherits = _inline_field(value, "workspace") == "true"
            dep.path = _inline_field(value, "path")
        else:
            dep = _Dependency(key=key_parts[0])
        if section == ["workspace", "dependencies"]:
            manifest.workspace_dependencies[dep.key] = dep
        else:
            manifest.dependencies.append(dep)
    return manifest


def _package_names(
    manifest: _Manifest, workspace: Optional[_Manifest] = None
) -> list[str]:
    """Return the package names a manifest depends on, as Cargo.lock records them.

    A rename (``package = "serde"``) and an inherited workspace dependency
    resolve to the real package name.
    """
    inherited = (workspace or manifest).workspace_dependencies
    names: list[str] = []
    for dep in manifest.dependencies:
        source = dep
        if dep.inherits and dep.key in inherited:
            source = inherited[dep.key]
        names.append(dep.package or source.package or dep.key)
    return names


def _parse_cargo_toml_deps(path: str) -> list[str]:
    """Return the package names a Cargo.toml depends on."""
    return _package_names(_parse_manifest(path))


def _parse_cargo_lock_names(lines: list[str]) -> set[str]:
    """Return set of package names from already-read Cargo.lock lines."""
    names: set[str] = set()
    for raw_line in lines:
        line = raw_line.strip()
        if line.startswith("name = "):
            names.add(line[len("name = ") :].strip().strip('"'))
    return names


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _is_within(path: str, directory: str) -> bool:
    path, directory = os.path.normpath(path), os.path.normpath(directory)
    return path == directory or path.startswith(directory + os.sep)


def _workspace_member_dirs(ws_dir: str, workspace: _Manifest) -> set[str]:
    """Return the member directories of the workspace rooted at ``ws_dir``.

    Follows Cargo: directories matched by a ``members`` glob, plus every
    path dependency (of the root package or of any member, transitively,
    including paths inherited from ``[workspace.dependencies]``) that lies
    inside the workspace directory, less anything under an
    ``exclude`` entry.
    Source: https://doc.rust-lang.org/cargo/reference/workspaces.html
    """
    excluded = [os.path.join(ws_dir, e) for e in workspace.exclude]

    def eligible(directory: str) -> bool:
        return (
            _is_within(directory, ws_dir)
            and not any(_is_within(directory, e) for e in excluded)
            and os.path.isfile(os.path.join(directory, "Cargo.toml"))
        )

    def path_dependencies(directory: str, manifest: _Manifest) -> list[str]:
        # An inherited dependency (`helper.workspace = true`) takes its path
        # from [workspace.dependencies], relative to the workspace root.
        paths: list[str] = []
        for dep in manifest.dependencies:
            if dep.path:
                paths.append(os.path.join(directory, dep.path))
            elif dep.inherits:
                inherited = workspace.workspace_dependencies.get(dep.key)
                if inherited is not None and inherited.path:
                    paths.append(os.path.join(ws_dir, inherited.path))
        return paths

    queue: list[str] = []
    for pattern in workspace.members:
        queue.extend(glob.glob(os.path.join(ws_dir, pattern)))
    queue.extend(path_dependencies(ws_dir, workspace))

    members: set[str] = set()
    while queue:
        directory = os.path.normpath(queue.pop())
        if directory in members or not eligible(directory):
            continue
        members.add(directory)
        manifest = _parse_manifest(os.path.join(directory, "Cargo.toml"))
        queue.extend(path_dependencies(directory, manifest))
    return members


def _workspace_root(crate_dir: str, root: str) -> Optional[tuple[str, _Manifest]]:
    """Return the workspace root that ``crate_dir`` is a member of, and its manifest.

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
        toml_path = os.path.join(current, "Cargo.toml")
        if not os.path.isfile(toml_path):
            continue
        workspace = _parse_manifest(toml_path)
        if workspace.has_workspace:
            members = _workspace_member_dirs(current, workspace)
            if os.path.normpath(os.path.abspath(crate_dir)) in members:
                return current, workspace
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
        manifest = _parse_manifest(toml_path)
        if not manifest.dependencies:
            return None

        found = None if manifest.has_workspace else _workspace_root(dir_path, root)
        ws_dir = found[0] if found else None
        deps = _package_names(manifest, found[1] if found else None)
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
