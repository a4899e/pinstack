"""package.json checker: enforces exact version pinning in all dependency sections."""

from __future__ import annotations

import json
import os

from pinstack.core import Checker, Finding, FileIndex

# Version prefixes that indicate a non-exact (unpinned) dependency.
_UNPINNED_PREFIXES = ("^", "~", ">=", "<=", ">", "<")

# Protocols and schemes that should never be flagged.
_SAFE_PREFIXES = ("workspace:", "file:", "http://", "https://", "git+", "git://", "github:")

_DEP_SECTIONS = (
    "dependencies",
    "devDependencies",
    "peerDependencies",
    "optionalDependencies",
)

# Lock files that provide integrity verification for package.json
_LOCK_FILES = frozenset(["package-lock.json", "yarn.lock", "pnpm-lock.yaml"])


def _is_unpinned(version: str) -> bool:
    """Return True if the version string uses a range operator."""
    if not version or not isinstance(version, str):
        return False
    # Safe protocols are never flagged
    for safe in _SAFE_PREFIXES:
        if version.startswith(safe):
            return False
    for prefix in _UNPINNED_PREFIXES:
        if version.startswith(prefix):
            return True
    return False


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
        packages = data.get("packages", {})
        for key in packages:
            if not key:
                continue  # skip root "" entry
            # Strip node_modules/ prefix (possibly nested)
            name = key
            while name.startswith("node_modules/"):
                name = name[len("node_modules/"):]
            if name:
                names.add(name)
    elif lock_filename == "yarn.lock":
        # Non-indented lines ending with : are package headers
        # e.g. "express@4.18.2:" or "express@^4.18.2, express@>=4.0.0:"
        for line in content.splitlines():
            if not line or line[0] in (' ', '\t', '#'):
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
        # Package entries under packages: like /express@4.18.2: or /express/4.18.2:
        in_packages = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped == "packages:":
                in_packages = True
                continue
            if in_packages:
                # End of packages section: non-indented line that isn't a package entry
                if line and not line[0].isspace():
                    in_packages = False
                    continue
                # Package entry lines start with / after stripping
                if stripped.startswith("/"):
                    entry = stripped.lstrip("/").rstrip(":")
                    # Format: name@version or @scope/name@version
                    # Find last @ that separates name from version
                    at_pos = entry.rfind("@")
                    if at_pos > 0:
                        names.add(entry[:at_pos])
                    else:
                        names.add(entry)

    return names


class PackageJsonChecker(Checker):
    name = "package_json"
    description = "Checks package.json files for unpinned (non-exact) dependency versions"
    patterns: list[str] = ["package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"]

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
                    if _is_unpinned(version):
                        findings.append(Finding(
                            checker=self.name,
                            path=rel_path,
                            line=0,
                            message=(
                                "'{}' in {} has unpinned version '{}'; use an exact version".format(
                                    pkg, section, version
                                )
                            ),
                        ))

            # Check for companion lock file
            found_lock_files = files & _LOCK_FILES
            if has_deps and not found_lock_files:
                findings.append(Finding(
                    checker=self.name,
                    path=rel_path,
                    line=0,
                    message="package.json has dependencies but no lock file (package-lock.json, yarn.lock, or pnpm-lock.yaml)",
                ))
            elif has_deps and found_lock_files:
                # Cross-reference: check that every manifest dep appears in the lock file
                lock_filename = sorted(found_lock_files)[0]
                lock_names = _extract_js_lock_names(full_path, lock_filename)
                if lock_names:
                    missing: list[str] = []
                    for section in _DEP_SECTIONS:
                        deps = data.get(section)
                        if not deps or not isinstance(deps, dict):
                            continue
                        for pkg in sorted(deps.keys()):
                            if pkg not in lock_names:
                                missing.append(pkg)
                    if missing:
                        findings.append(Finding(
                            checker=self.name,
                            path=rel_path,
                            line=0,
                            message="{} is stale: missing {} ({})".format(
                                lock_filename,
                                "{} dependency".format(len(missing)) if len(missing) == 1 else "{} dependencies".format(len(missing)),
                                ", ".join(sorted(missing)),
                            ),
                        ))

        return findings
