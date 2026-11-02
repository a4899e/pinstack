"""package.json checker: enforces exact version pinning in all dependency sections."""

from __future__ import annotations

import json
import os
import re

from pinstack.core import (
    Checker,
    Finding,
    FileIndex,
    describe_missing,
    empty_lock_finding,
)

# Version prefixes that indicate a non-exact (unpinned) dependency.
_UNPINNED_PREFIXES = ("^", "~", ">=", "<=", ">", "<")

# Local references that should not be flagged (resolved at install time).
_SAFE_PREFIXES = ("workspace:", "file:")

# URL-based sources that are not content-addressed and should be flagged.
_URL_PREFIXES = ("http://", "https://", "git+", "git://", "github:")

_DEP_SECTIONS = (
    "dependencies",
    "devDependencies",
    "peerDependencies",
    "optionalDependencies",
)

_PNPM_V5_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+")

# Lock files that provide integrity verification for package.json
_LOCK_FILES = frozenset(["package-lock.json", "yarn.lock", "pnpm-lock.yaml"])


def _is_unpinned(version: str) -> tuple[bool, str]:
    """Return (is_bad, reason) for a version string."""
    if not version or not isinstance(version, str):
        return False, ""
    for safe in _SAFE_PREFIXES:
        if version.startswith(safe):
            return False, ""
    for prefix in _URL_PREFIXES:
        if version.startswith(prefix):
            return True, "not content-addressed"
    for prefix in _UNPINNED_PREFIXES:
        if version.startswith(prefix):
            return True, "unpinned version"
    return False, ""


def _extract_js_lock_names(full_path: str, lock_filename: str) -> set[str]:
    """Extract package names from a JS lock file."""
    lock_path = os.path.join(os.path.dirname(full_path), lock_filename)
    names: set[str] = set()
    try:
        with open(lock_path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except OSError:
        return names

    if lock_filename == "package-lock.json":
        try:
            data = json.loads(content)
        except (ValueError, TypeError):
            return names
        if not isinstance(data, dict):
            return names
        # lockfileVersion 2 and 3 list "node_modules/<name>" paths under
        # "packages"; lockfileVersion 1 (and v2, for compatibility) maps
        # top-level names under "dependencies".
        packages = data.get("packages")
        if isinstance(packages, dict):
            for key in packages:
                if not key:
                    continue  # skip root "" entry
                # The installed name is the part after the last node_modules/
                name = key.rsplit("node_modules/", 1)[-1]
                if name:
                    names.add(name)
        dependencies = data.get("dependencies")
        if isinstance(dependencies, dict):
            names.update(k for k in dependencies if k)
    elif lock_filename == "yarn.lock":
        # Non-indented lines ending with : are package headers
        # e.g. "express@4.18.2:" or "express@^4.18.2, express@>=4.0.0:"
        for line in content.splitlines():
            if not line or line[0] in (" ", "\t", "#"):
                continue
            if line.endswith(":"):
                # First entry before comma; extract name before @version
                entry = line.rstrip(":").split(",")[0].strip().strip('"')
                # Package name is everything before the last @ (scoped pkgs have leading @)
                at_pos = entry.rfind("@")
                if at_pos > 0:
                    names.add(entry[:at_pos])
                else:
                    names.add(entry)
    elif lock_filename == "pnpm-lock.yaml":
        # Entries under "packages:" are keyed by name and version:
        #   v5:      /express/4.18.2:   /react-dom/18.2.0_react@18.2.0:
        #   v6-v8:   /express@4.18.2:   /@scope/pkg@1.0.0(peer@1.0.0):
        #   v9:      express@4.18.2:    '@scope/pkg@1.0.0':
        in_packages = False
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped:
                continue  # blank lines separate entries
            if line[0] not in (" ", "\t"):
                in_packages = stripped == "packages:"
                continue
            if not in_packages or not stripped.endswith(":"):
                continue
            # Package keys are the least-indented lines in the section.
            if len(line) - len(line.lstrip()) != 2:
                continue
            entry = stripped[:-1].strip("'\"").lstrip("/").split("(", 1)[0]
            # v5 "name/version[_peer@x]": the last segment is the version.
            name, sep, last = entry.rpartition("/")
            if sep and _PNPM_V5_VERSION_RE.match(last):
                names.add(name)
                continue
            at_pos = entry.rfind("@")
            if at_pos > 0:
                names.add(entry[:at_pos])
            elif entry:
                names.add(entry)

    return names


_YARN_LOCK_MARKERS = re.compile(r"^(?:# yarn lockfile v1|__metadata:)", re.M)
_PNPM_LOCK_MARKER = re.compile(r"^lockfileVersion:", re.M)
# Specs resolved from the working tree; lock files need not list them as
# registry packages (pnpm records them only under importers).
_LOCAL_SPEC_PREFIXES = ("file:", "link:", "workspace:", "portal:", ".", "/", "~/")


def _js_lock_recognised(full_path: str, lock_filename: str) -> bool:
    """Return True if a lock file is in a format its package manager writes.

    A recognised lock may legitimately list no packages (only local links,
    or only optional peers that were not installed). An empty or
    unrecognised file may not.
    """
    lock_path = os.path.join(os.path.dirname(full_path), lock_filename)
    try:
        with open(lock_path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except OSError:
        return False
    if lock_filename == "package-lock.json":
        try:
            data = json.loads(content)
        except (ValueError, TypeError):
            return False
        return isinstance(data, dict) and isinstance(data.get("lockfileVersion"), int)
    if lock_filename == "yarn.lock":
        return bool(_YARN_LOCK_MARKERS.search(content))
    return bool(_PNPM_LOCK_MARKER.search(content))


def _needs_lock_entry(data: dict, section: str, pkg: str, spec: object) -> bool:
    """Return True if a declared dependency must appear in the lock file.

    Local references are resolved from the tree, and an optional peer
    (``peerDependenciesMeta.<pkg>.optional``) is not installed, so neither
    has to be listed as a locked package.
    """
    if isinstance(spec, str) and spec.strip().startswith(_LOCAL_SPEC_PREFIXES):
        return False
    if section == "peerDependencies":
        meta = data.get("peerDependenciesMeta")
        entry = meta.get(pkg) if isinstance(meta, dict) else None
        if isinstance(entry, dict) and entry.get("optional") is True:
            return False
    return True


class PackageJsonChecker(Checker):
    name = "package_json"
    description = (
        "Checks package.json files for unpinned (non-exact) dependency versions"
    )
    patterns: list[str] = [
        "package.json",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
    ]

    def check(self, index: FileIndex, root: str) -> list[Finding]:
        findings: list[Finding] = []

        for dir_path in sorted(index.keys()):
            files = index[dir_path]
            if "package.json" not in files:
                continue

            full_path = os.path.join(dir_path, "package.json")
            rel_path = os.path.relpath(full_path, root)

            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                    data = json.load(fh)
            except (OSError, ValueError):
                continue

            if not isinstance(data, dict):
                continue

            has_deps = False
            for section in _DEP_SECTIONS:
                deps = data.get(section)
                if not deps or not isinstance(deps, dict):
                    continue
                has_deps = True
                for pkg, version in sorted(deps.items()):
                    is_bad, reason = _is_unpinned(version)
                    if is_bad:
                        integrity = reason == "not content-addressed"
                        findings.append(
                            Finding(
                                checker=self.name,
                                path=rel_path,
                                line=0,
                                message="'{}' in {} has {} '{}'; use an exact version".format(
                                    pkg, section, reason, version
                                ),
                                integrity=integrity,
                            )
                        )

            # Check for companion lock file
            found_lock_files = files & _LOCK_FILES
            if has_deps and not found_lock_files:
                findings.append(
                    Finding(
                        checker=self.name,
                        path=rel_path,
                        line=0,
                        message="package.json has dependencies but no lock file (package-lock.json, yarn.lock, or pnpm-lock.yaml)",
                        integrity=True,
                    )
                )
            elif has_deps and found_lock_files:
                # Cross-reference: every manifest dep must appear in some lock
                # file. Each lock that yields no packages and is not in a
                # recognised format is reported, and the cross-reference still
                # runs against the rest.
                lock_filenames = sorted(found_lock_files)
                lock_names: set[str] = set()
                for lock_filename in lock_filenames:
                    names = _extract_js_lock_names(full_path, lock_filename)
                    if not names and not _js_lock_recognised(full_path, lock_filename):
                        findings.append(
                            empty_lock_finding(
                                self.name,
                                os.path.relpath(
                                    os.path.join(dir_path, lock_filename), root
                                ),
                            )
                        )
                    lock_names |= names
                missing: list[str] = []
                for section in _DEP_SECTIONS:
                    deps = data.get(section)
                    if not deps or not isinstance(deps, dict):
                        continue
                    for pkg in sorted(deps.keys()):
                        if pkg in lock_names:
                            continue
                        if _needs_lock_entry(data, section, pkg, deps[pkg]):
                            missing.append(pkg)
                if missing:
                    findings.append(
                        Finding(
                            checker=self.name,
                            path=rel_path,
                            line=0,
                            message="{} {} stale: {}".format(
                                ", ".join(lock_filenames),
                                "is" if len(lock_filenames) == 1 else "are",
                                describe_missing(missing),
                            ),
                            integrity=True,
                        )
                    )

        return findings
