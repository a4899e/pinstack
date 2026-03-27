"""Helm checker: enforces Chart.lock existence when dependencies declared, and digest presence."""

from __future__ import annotations

import os
from typing import Optional

from pinstack.core import Checker, Finding, FileIndex


class HelmChecker(Checker):
    name = "helm"
    description = (
        "Checks Chart.yaml for missing Chart.lock and Chart.lock for missing digests"
    )
    patterns: list[str] = ["Chart.yaml", "Chart.lock"]

    def check(self, index: FileIndex, root: str) -> list[Finding]:
        findings: list[Finding] = []

        # Collect all directories that have at least one helm file
        helm_dirs: set[str] = set()
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
                    findings.append(
                        Finding(
                            checker="helm",
                            path=chart_yaml_rel,
                            line=0,
                            message="Chart.yaml declares dependencies but Chart.lock is missing; run 'helm dependency update'",
                            integrity=True,
                        )
                    )

            # Check Chart.lock for missing digests and cross-reference deps
            if has_chart_lock:
                chart_lock_path = os.path.join(dir_path, "Chart.lock")
                chart_lock_rel = os.path.relpath(chart_lock_path, root)
                lock_findings = self._check_lock_digests(
                    chart_lock_path, chart_lock_rel
                )
                findings.extend(lock_findings)

                # Cross-reference: every dep in Chart.yaml must appear in Chart.lock
                if has_chart_yaml:
                    yaml_deps = self._parse_dep_names(chart_yaml_path)
                    lock_deps = self._parse_dep_names(chart_lock_path)
                    missing = [dep for dep in sorted(yaml_deps) if dep not in lock_deps]
                    if missing:
                        findings.append(
                            Finding(
                                checker="helm",
                                path=chart_lock_rel,
                                line=0,
                                message="Chart.lock is stale: missing {} ({})".format(
                                    "{} dependency".format(len(missing))
                                    if len(missing) == 1
                                    else "{} dependencies".format(len(missing)),
                                    ", ".join(sorted(missing)),
                                ),
                                integrity=True,
                            )
                        )

        return findings

    def _has_dependencies(self, path: str) -> bool:
        """Return True if Chart.yaml contains a top-level 'dependencies:' line."""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    stripped = line.rstrip()
                    if stripped == "dependencies:" or stripped.startswith(
                        "dependencies:"
                    ):
                        return True
        except OSError:
            pass
        return False

    def _parse_dep_names(self, path: str) -> set[str]:
        """Parse a Chart.yaml or Chart.lock and return the set of dependency names.

        Looks for lines of the form '- name: <value>' that appear after a
        'dependencies:' section header.
        """
        names: set[str] = set()
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                in_deps = False
                for line in fh:
                    stripped = line.rstrip()
                    bare = stripped.lstrip()
                    if bare == "dependencies:" or bare.startswith("dependencies:"):
                        in_deps = True
                        continue
                    if in_deps:
                        # A top-level non-indented, non-list key ends the section
                        if (
                            stripped
                            and not stripped[0].isspace()
                            and not stripped.startswith("-")
                        ):
                            in_deps = False
                            continue
                        if bare.startswith("- name:"):
                            name = bare[len("- name:") :].strip()
                            if name:
                                names.add(name)
        except OSError:
            pass
        return names

    def _check_lock_digests(self, path: str, rel_path: str) -> list[Finding]:
        """Parse Chart.lock line-by-line; warn if any dependency block lacks a digest: field.

        Chart.lock format has per-dependency digest: fields (one per dep entry).
        Some older or tool-generated Chart.lock files instead carry a single
        top-level digest: field (after the generated: line) that covers the
        entire dependency set.  Either form is accepted as valid — if ANY
        digest: line is present in the file we do not flag it as missing.
        """
        findings: list[Finding] = []

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except OSError:
            return findings

        # First pass: check for a top-level (non-indented) digest: line.
        # If one exists the whole lock is considered verified; no per-dep
        # checks are needed.
        for raw_line in lines:
            stripped_bare = raw_line.lstrip()
            bare = raw_line.rstrip()
            if not bare[0:1].isspace() and stripped_bare.startswith("digest:"):
                # Top-level digest covers the full dependency set — accept as valid.
                return findings

        # Each dependency entry starts with "- name:" and ends when the next
        # "- name:" or end-of-dependencies section is reached.
        dep_name: Optional[str] = None
        dep_line = 0
        has_digest = False

        for lineno, raw_line in enumerate(lines, start=1):
            line = raw_line.rstrip()
            stripped = line.lstrip()

            if stripped.startswith("- name:"):
                # Flush previous dep
                if dep_name is not None and not has_digest:
                    findings.append(
                        Finding(
                            checker="helm",
                            path=rel_path,
                            line=dep_line,
                            message="dependency '{}' in Chart.lock is missing digest: field".format(
                                dep_name
                            ),
                            integrity=True,
                        )
                    )
                dep_name = stripped[len("- name:") :].strip()
                dep_line = lineno
                has_digest = False
                continue

            if dep_name is not None and stripped.startswith("digest:"):
                has_digest = True

        # Flush last dep
        if dep_name is not None and not has_digest:
            findings.append(
                Finding(
                    checker="helm",
                    path=rel_path,
                    line=dep_line,
                    message="dependency '{}' in Chart.lock is missing digest: field".format(
                        dep_name
                    ),
                    integrity=True,
                )
            )

        return findings
