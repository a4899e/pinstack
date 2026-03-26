"""package-lock.json checker: warns when packages are missing an integrity hash."""

from __future__ import annotations

import json
import os

from pinstack.core import Checker, Finding, FileIndex


class PackageLockChecker(Checker):
    name = "package_lock"
    description = "Checks package-lock.json files for packages missing integrity hashes"
    patterns: list[str] = ["package-lock.json"]

    def check(self, index: FileIndex, root: str) -> list[Finding]:
        findings: list[Finding] = []

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
                if packages and isinstance(packages, dict):
                    # v2/v3 format: flat "packages" map keyed by "node_modules/<name>"
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
                                integrity=True,
                            ))
                else:
                    # v1 format: recursive "dependencies" map keyed by package name
                    dependencies = data.get("dependencies")
                    if dependencies and isinstance(dependencies, dict):
                        self._check_v1_deps(dependencies, rel_path, findings)

        return findings

    def _check_v1_deps(self, deps: dict, rel_path: str, findings: list[Finding]) -> None:
        """Recursively walk a v1 lockfile dependencies dict, appending findings."""
        for pkg_name in sorted(deps.keys()):
            pkg_data = deps[pkg_name]
            if not isinstance(pkg_data, dict):
                continue
            if "integrity" not in pkg_data:
                findings.append(Finding(
                    checker=self.name,
                    path=rel_path,
                    line=0,
                    message=(
                        "'{}' is missing an integrity hash in package-lock.json".format(pkg_name)
                    ),
                    integrity=True,
                ))
            # Recurse into nested dependencies (bundled/hoisted)
            nested = pkg_data.get("dependencies")
            if nested and isinstance(nested, dict):
                self._check_v1_deps(nested, rel_path, findings)
