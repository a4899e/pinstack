"""package-lock.json checker: warns when packages are missing an integrity hash."""

import json
import os
from typing import Dict, List, Set

from pinstack.core import Checker, Finding

FileIndex = Dict[str, Set[str]]


class PackageLockChecker(Checker):
    name = "package_lock"
    description = "Checks package-lock.json files for packages missing integrity hashes"
    patterns = ["package-lock.json"]  # type: List[str]

    def check(self, index, root):
        # type: (FileIndex, str) -> List[Finding]
        findings = []  # type: List[Finding]

        for dir_path in sorted(index.keys()):
            for fname in sorted(index[dir_path]):
                if fname != "package-lock.json":
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

                packages = data.get("packages")
                if not packages or not isinstance(packages, dict):
                    continue

                for pkg_key in sorted(packages.keys()):
                    # Skip the root entry (empty string key)
                    if pkg_key == "":
                        continue
                    pkg_data = packages[pkg_key]
                    if not isinstance(pkg_data, dict):
                        continue
                    # Skip local symlinks
                    if pkg_data.get("link") is True:
                        continue
                    if "integrity" not in pkg_data:
                        findings.append(Finding(
                            checker=self.name,
                            path=rel_path,
                            line=0,
                            message=(
                                "'{}' is missing an integrity hash in package-lock.json".format(pkg_key)
                            ),
                        ))

        return findings
