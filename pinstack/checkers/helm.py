"""Helm checker: enforces Chart.lock existence when dependencies declared, and digest presence."""

import fnmatch
import os
from typing import Dict, List, Set

from pinstack.core import Checker, Finding, Severity

FileIndex = Dict[str, Set[str]]


class HelmChecker(Checker):
    name = "helm"
    description = "Checks Chart.yaml for missing Chart.lock and Chart.lock for missing digests"
    patterns = ["Chart.yaml", "Chart.lock"]  # type: List[str]

    def check(self, index, root):
        # type: (FileIndex, str) -> List[Finding]
        findings = []  # type: List[Finding]

        # Collect all directories that have at least one helm file
        helm_dirs = set()  # type: Set[str]
        for dir_path in index.keys():
            for fname in index[dir_path]:
                if fname in ("Chart.yaml", "Chart.lock"):
                    helm_dirs.add(dir_path)

        for dir_path in sorted(helm_dirs):
            present = index[dir_path]
            has_chart_yaml = "Chart.yaml" in present
            has_chart_lock = "Chart.lock" in present

            # Check Chart.yaml for dependencies section
            if has_chart_yaml:
                chart_yaml_path = os.path.join(dir_path, "Chart.yaml")
                chart_yaml_rel = os.path.relpath(chart_yaml_path, root)
                has_dependencies = self._has_dependencies(chart_yaml_path)

                if has_dependencies and not has_chart_lock:
                    findings.append(Finding(
                        checker="helm",
                        path=chart_yaml_rel,
                        line=0,
                        severity=Severity.WARNING,
                        message="Chart.yaml declares dependencies but Chart.lock is missing; run 'helm dependency update'",
                    ))

            # Check Chart.lock for missing digests
            if has_chart_lock:
                chart_lock_path = os.path.join(dir_path, "Chart.lock")
                chart_lock_rel = os.path.relpath(chart_lock_path, root)
                lock_findings = self._check_lock_digests(chart_lock_path, chart_lock_rel)
                findings.extend(lock_findings)

        return findings

    def _has_dependencies(self, path):
        # type: (str) -> bool
        """Return True if Chart.yaml contains a top-level 'dependencies:' line."""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    stripped = line.rstrip()
                    if stripped == "dependencies:" or stripped.startswith("dependencies:"):
                        return True
        except OSError:
            pass
        return False

    def _check_lock_digests(self, path, rel_path):
        # type: (str, str) -> List[Finding]
        """Parse Chart.lock line-by-line; warn if any dependency block lacks a digest: field."""
        findings = []  # type: List[Finding]

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except OSError:
            return findings

        # Each dependency entry starts with "- name:" and ends when the next
        # "- name:" or end-of-dependencies section is reached.
        dep_name = None   # type: str
        dep_line = 0
        has_digest = False

        for lineno, raw_line in enumerate(lines, start=1):
            line = raw_line.rstrip()
            stripped = line.lstrip()

            if stripped.startswith("- name:"):
                # Flush previous dep
                if dep_name is not None and not has_digest:
                    findings.append(Finding(
                        checker="helm",
                        path=rel_path,
                        line=dep_line,
                        severity=Severity.WARNING,
                        message="dependency '{}' in Chart.lock is missing digest: field".format(dep_name),
                    ))
                dep_name = stripped[len("- name:"):].strip()
                dep_line = lineno
                has_digest = False
                continue

            if dep_name is not None and stripped.startswith("digest:"):
                has_digest = True

        # Flush last dep
        if dep_name is not None and not has_digest:
            findings.append(Finding(
                checker="helm",
                path=rel_path,
                line=dep_line,
                severity=Severity.WARNING,
                message="dependency '{}' in Chart.lock is missing digest: field".format(dep_name),
            ))

        return findings
