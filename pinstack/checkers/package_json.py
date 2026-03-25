"""package.json checker: enforces exact version pinning in all dependency sections."""

import json
import os
from typing import Dict, List, Set

from pinstack.core import Checker, Finding

FileIndex = Dict[str, Set[str]]

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


def _is_unpinned(version):
    # type: (str) -> bool
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


class PackageJsonChecker(Checker):
    name = "package_json"
    description = "Checks package.json files for unpinned (non-exact) dependency versions"
    patterns = ["package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"]  # type: List[str]

    def check(self, index, root):
        # type: (FileIndex, str) -> List[Finding]
        findings = []  # type: List[Finding]

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
            if has_deps and not (files & _LOCK_FILES):
                findings.append(Finding(
                    checker=self.name,
                    path=rel_path,
                    line=0,
                    message="package.json has dependencies but no lock file (package-lock.json, yarn.lock, or pnpm-lock.yaml)",
                ))

        return findings
