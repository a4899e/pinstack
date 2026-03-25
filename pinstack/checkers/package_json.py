"""package.json checker: enforces exact version pinning in all dependency sections."""

import json
import os
from typing import Dict, List, Set

from pinstack.core import Checker, Finding, Severity

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
    patterns = ["package.json"]  # type: List[str]

    def check(self, index, root):
        # type: (FileIndex, str) -> List[Finding]
        findings = []  # type: List[Finding]

        for dir_path in sorted(index.keys()):
            for fname in sorted(index[dir_path]):
                if fname != "package.json":
                    continue

                full_path = os.path.join(dir_path, fname)
                rel_path = os.path.relpath(full_path, root)

                try:
                    with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                        data = json.load(fh)
                except (OSError, ValueError):
                    continue

                if not isinstance(data, dict):
                    continue

                for section in _DEP_SECTIONS:
                    deps = data.get(section)
                    if not deps or not isinstance(deps, dict):
                        continue
                    for pkg, version in sorted(deps.items()):
                        if _is_unpinned(version):
                            findings.append(Finding(
                                checker=self.name,
                                path=rel_path,
                                line=0,
                                severity=Severity.ERROR,
                                message=(
                                    "'{}' in {} has unpinned version '{}'; use an exact version".format(
                                        pkg, section, version
                                    )
                                ),
                            ))

        return findings
