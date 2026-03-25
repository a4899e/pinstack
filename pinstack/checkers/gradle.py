"""Gradle checker: enforces pinned versions in build.gradle / build.gradle.kts files
and requires a gradle.lockfile companion."""

import os
import re
from typing import Dict, Set

from pinstack.core import Checker, Finding

FileIndex = Dict[str, Set[str]]

# Dependency configurations to match (Groovy and Kotlin DSL)
_CONFIGS = (
    "implementation",
    "api",
    "compile",
    "testImplementation",
    "testCompile",
    "runtimeOnly",
    "compileOnly",
    "annotationProcessor",
    "kapt",
    "testRuntimeOnly",
    "testCompileOnly",
)

# Matches:
#   implementation 'group:artifact:version'
#   implementation("group:artifact:version")
#   testImplementation 'group:artifact'   (no version)
#   etc.
# Group 1: configuration name
# Group 2: dependency string (content between quotes)
_DEP_RE = re.compile(
    r"""^\s*(?P<config>""" + "|".join(re.escape(c) for c in _CONFIGS) + r""")\s*[(\s]"""
    r"""['"]((?P<group>[^'":\s]+):(?P<artifact>[^'":\s]+)(?::(?P<version>[^'"]*))?)['"]"""
)

# Patterns that indicate a dynamic/bad version in the version segment
_RANGE_RE = re.compile(r"[\[\]()]")


def _is_bad_version(version):
    # type: (str) -> str
    """Return a human-readable reason string if version is bad, else empty string."""
    if not version:
        return "missing version"
    v = version.strip()
    if not v:
        return "missing version"
    # Dynamic + suffix or bare +
    if v == "+" or v.endswith("+"):
        return "dynamic version '{}' (+ wildcard)".format(v)
    # latest.release or latest.integration
    if v.lower() in ("latest.release", "latest.integration"):
        return "dynamic version '{}'".format(v)
    # Version range: contains [, ], (, )
    if _RANGE_RE.search(v):
        return "version range '{}'".format(v)
    return ""


def _parse_lockfile_coords(path):
    # type: (str) -> Set[str]
    """Parse a gradle.lockfile and return a set of 'group:artifact' strings.

    Each non-comment, non-empty line has the form:
        group:artifact:version=configurations
    or the sentinel:
        empty=
    Lines starting with '#' and the 'empty=' sentinel are skipped.
    """
    coords = set()  # type: Set[str]
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or line == "empty=":
                    continue
                # Format: group:artifact:version=configs
                parts = line.split(":")
                if len(parts) >= 2:
                    coords.add("{}:{}".format(parts[0], parts[1]))
    except OSError:
        pass
    return coords


class GradleChecker(Checker):
    name = "gradle"
    description = (
        "Checks build.gradle/build.gradle.kts for unpinned dependency versions "
        "and requires a gradle.lockfile"
    )
    patterns = ["build.gradle", "build.gradle.kts", "gradle.lockfile"]  # type: List[str]

    def check(self, index, root):
        # type: (FileIndex, str) -> List[Finding]
        findings = []  # type: List[Finding]

        for dir_path in sorted(index.keys()):
            files = index[dir_path]

            has_build_gradle = "build.gradle" in files
            has_build_gradle_kts = "build.gradle.kts" in files
            has_lockfile = "gradle.lockfile" in files

            build_files = []  # type: List[str]
            if has_build_gradle:
                build_files.append("build.gradle")
            if has_build_gradle_kts:
                build_files.append("build.gradle.kts")

            if not build_files:
                # Only lockfile or nothing — no action
                continue

            # Check for missing lockfile
            if not has_lockfile:
                # Report against the first build file found
                first_build = build_files[0]
                rel_path = os.path.relpath(os.path.join(dir_path, first_build), root)
                findings.append(Finding(
                    checker=self.name,
                    path=rel_path,
                    line=0,
                    message="{} has no gradle.lockfile; run 'gradle dependencies --write-locks'".format(first_build),
                ))

            # Parse lockfile coords once (if present) for cross-referencing
            lock_coords = set()  # type: Set[str]
            if has_lockfile:
                lock_path = os.path.join(dir_path, "gradle.lockfile")
                lock_coords = _parse_lockfile_coords(lock_path)

            # Check dependency versions in each build file
            missing_from_lock = []  # type: List[str]
            for fname in build_files:
                full_path = os.path.join(dir_path, fname)
                rel_path = os.path.relpath(full_path, root)

                try:
                    with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                        lines = fh.readlines()
                except OSError:
                    continue

                for lineno, raw_line in enumerate(lines, start=1):
                    m = _DEP_RE.match(raw_line)
                    if not m:
                        continue

                    group = m.group("group") or ""
                    artifact = m.group("artifact") or ""
                    version = m.group("version")  # None if no version segment
                    coord = "{}:{}".format(group, artifact)

                    if version is None:
                        findings.append(Finding(
                            checker=self.name,
                            path=rel_path,
                            line=lineno,
                            message="'{}' has no version specified; pin to an exact version".format(coord),
                        ))
                        continue

                    reason = _is_bad_version(version)
                    if reason:
                        findings.append(Finding(
                            checker=self.name,
                            path=rel_path,
                            line=lineno,
                            message="'{}' uses {}; pin to an exact version".format(coord, reason),
                        ))
                        continue

                    # Version is pinned and valid: collect for cross-reference
                    if has_lockfile and coord not in lock_coords:
                        missing_from_lock.append(coord)

            if missing_from_lock:
                lock_rel = os.path.relpath(os.path.join(dir_path, "gradle.lockfile"), root)
                findings.append(Finding(
                    checker=self.name,
                    path=lock_rel,
                    line=0,
                    message="gradle.lockfile is stale: missing {} ({})".format(
                        "{} dependency".format(len(missing_from_lock)) if len(missing_from_lock) == 1 else "{} dependencies".format(len(missing_from_lock)),
                        ", ".join(sorted(missing_from_lock)),
                    ),
                ))

        return findings
