"""Gradle checker: enforces pinned versions in build.gradle / build.gradle.kts files
and requires a gradle.lockfile companion."""

from __future__ import annotations

import fnmatch
import os
import re
from typing import Optional

from pinstack.core import Checker, Finding, FileIndex

# Dependency configurations to match (Groovy and Kotlin DSL)
_CONFIGS = (
    "implementation",
    "api",
    "compile",
    "compileOnlyApi",
    "runtime",
    "testImplementation",
    "testCompile",
    "testRuntime",
    "runtimeOnly",
    "compileOnly",
    "annotationProcessor",
    "kapt",
    "ksp",
    "testRuntimeOnly",
    "testCompileOnly",
    "testFixturesApi",
    "testFixturesImplementation",
    "developmentOnly",
    "classpath",
)
_CONFIG_PAT = "|".join(re.escape(c) for c in _CONFIGS)

# A BOM or test-fixtures wrapper around the coordinate string:
#   implementation platform("g:a:v")   implementation(enforcedPlatform("g:a:v"))
_WRAPPER = r"""(?:(?:enforcedPlatform|platform|testFixtures)\s*\(\s*)?"""

# Matches string notation:
#   implementation 'group:artifact:version'
#   implementation("group:artifact:version")
#   testImplementation 'group:artifact'   (no version)
#   implementation platform("group:artifact:version")
_DEP_RE = re.compile(
    r"""^\s*(?P<config>"""
    + _CONFIG_PAT
    + r""")\s*[(\s]\s*"""
    + _WRAPPER
    + r"""['"]((?P<group>[^'":\s]+):(?P<artifact>[^'":\s]+)(?::(?P<version>[^'"]*))?)['"]"""
)

# Map notation (Groovy) and named arguments (Kotlin DSL), fields in any order:
#   implementation group: 'com.google.guava', name: 'guava', version: '31.1-jre'
#   implementation(group = "org.foo", name = "lib", version = "1.0")
# Fields are found in one left-to-right pass over the line, so the cost is
# linear in its length.
_CONFIG_START_RE = re.compile(r"""^\s*(?P<config>""" + _CONFIG_PAT + r""")\b""")
_MAP_FIELD_RE = re.compile(
    r"""\b(?P<key>group|name|version)\s*[:=]\s*['"](?P<value>[^'"]*)['"]"""
)

# Patterns that indicate a dynamic/bad version in the version segment
_RANGE_RE = re.compile(r"[\[\]()]")


def _match_map_notation(line: str) -> Optional[tuple[str, str, Optional[str]]]:
    """Return (group, name, version or None) for a map-notation declaration."""
    if not _CONFIG_START_RE.match(line):
        return None
    fields: dict[str, str] = {}
    for m in _MAP_FIELD_RE.finditer(line):
        fields.setdefault(m.group("key"), m.group("value"))
    if not fields.get("group") or not fields.get("name"):
        return None
    return fields["group"], fields["name"], fields.get("version")


def _split_statements(line: str) -> list[str]:
    """Split a line at ``{``, ``}`` and ``;`` outside string literals.

    Declarations can share a line with block braces or with each other
    (``dependencies { implementation "g:a:1"; api "g:b:2" }``), while
    ``"g:a:${v}"`` must stay whole.
    """
    statements: list[str] = []
    start = 0
    quote = ""
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in ("'", '"'):
            quote = ch
        elif ch in "{};":
            statements.append(line[start:i])
            start = i + 1
    statements.append(line[start:])
    return statements


def _declarations(
    lines: list[str],
) -> list[tuple[int, str, str, Optional[str]]]:
    """Return (line, group, artifact, version or None) for each declaration."""
    found: list[tuple[int, str, str, Optional[str]]] = []
    for lineno, raw_line in enumerate(lines, start=1):
        for statement in _split_statements(raw_line):
            m = _DEP_RE.match(statement)
            if m:
                found.append(
                    (
                        lineno,
                        m.group("group") or "",
                        m.group("artifact") or "",
                        m.group("version"),
                    )
                )
                continue
            parsed = _match_map_notation(statement)
            if parsed is not None:
                found.append((lineno,) + parsed)
    return found


def _is_bad_version(version: str) -> str:
    """Return a human-readable reason string if version is bad, else empty string."""
    if not version:
        return "missing version"
    v = version.strip()
    if not v:
        return "missing version"
    # $v / ${v}: resolved from a property or extra at build time
    if "$" in v:
        return "variable reference '{}' which cannot be verified".format(v)
    # Dynamic + suffix or bare +
    if v == "+" or v.endswith("+"):
        return "dynamic version '{}' (+ wildcard)".format(v)
    # latest.release or latest.integration
    if v.lower() in ("latest.release", "latest.integration"):
        return "dynamic version '{}'".format(v)
    # Version range: contains [, ], (, )
    if _RANGE_RE.search(v):
        return "version range '{}'".format(v)
    # SNAPSHOT versions are republished under the same version
    if v.upper().endswith("-SNAPSHOT"):
        return "SNAPSHOT version '{}'".format(v)
    return ""


def _parse_lockfile_coords(path: str) -> set[str]:
    """Parse a gradle.lockfile and return a set of 'group:artifact' strings.

    Each non-comment, non-empty line has the form:
        group:artifact:version=configurations
    or the sentinel:
        empty=
    Lines starting with '#' and the 'empty=' sentinel are skipped.
    """
    coords: set[str] = set()
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


_CATALOG_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*(?:#.*)?$")
_CATALOG_ENTRY_RE = re.compile(r"""^\s*([A-Za-z0-9_.\-]+)\s*=\s*(.+?)\s*$""")
_TOML_STRING_RE = re.compile(r"""^["']([^"']*)["']""")
_INLINE_FIELD_RE = re.compile(
    r"""\b(module|group|name|version|strictly|require|prefer)\s*=\s*["']([^"']*)["']"""
)
_VERSION_REF_RE = re.compile(r"\bversion\.ref\s*=")


def _catalog_findings(path: str, rel_path: str, checker: str) -> list[Finding]:
    """Check a Gradle version catalog (libs.versions.toml).

    ``[versions]`` entries, and the versions written inline in ``[libraries]``
    and ``[plugins]``, must be exact. A library with neither a version nor a
    ``version.ref`` resolves through a platform or constraint the catalog
    cannot show, so it is reported as missing a version. Rich versions
    (``{ strictly = ..., require = ..., prefer = ... }``) are checked field
    by field.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return []

    findings: list[Finding] = []
    section = ""
    for lineno, raw in enumerate(lines, start=1):
        if raw.lstrip().startswith("#"):
            continue
        line = raw
        m = _CATALOG_SECTION_RE.match(line)
        if m:
            section = m.group(1).strip()
            continue
        if section not in ("versions", "libraries", "plugins"):
            continue
        m = _CATALOG_ENTRY_RE.match(line)
        if not m:
            continue
        alias, value = m.group(1), m.group(2)

        versions: list[str] = []
        string_match = _TOML_STRING_RE.match(value)
        if string_match:
            text = string_match.group(1)
            if section == "versions":
                versions.append(text)
            elif section == "libraries":
                # "group:artifact:version"
                parts = text.split(":")
                versions.append(parts[2] if len(parts) >= 3 else "")
            else:
                # plugins: "id:version"
                versions.append(text.split(":", 1)[1] if ":" in text else "")
        elif value.startswith("{"):
            if _VERSION_REF_RE.search(value):
                continue  # checked where [versions] defines it
            fields = _INLINE_FIELD_RE.findall(value)
            versions = [
                v
                for k, v in fields
                if k in ("version", "strictly", "require", "prefer")
            ]
            if not versions:
                versions.append("")
        else:
            continue

        for version in versions:
            reason = _is_bad_version(version)
            if reason == "missing version":
                message = (
                    "'{}' in [{}] has no version specified; pin to an exact version"
                )
            elif reason:
                message = "'{}' in [{}] uses {}; pin to an exact version"
            else:
                continue
            findings.append(
                Finding(
                    checker=checker,
                    path=rel_path,
                    line=lineno,
                    message=message.format(alias, section, reason),
                )
            )
    return findings


_BUILD_FILES = ("build.gradle", "build.gradle.kts")
_SCRIPT_PATTERNS = ("*.gradle", "*.gradle.kts")
_CATALOG_PATTERN = "*.versions.toml"


class GradleChecker(Checker):
    name = "gradle"
    description = (
        "Checks Gradle build scripts and version catalogs for unpinned dependency"
        " versions and requires a gradle.lockfile"
    )
    # Every script, so files pulled in with `apply from:` are checked too.
    # The lock file requirement is keyed on build.gradle(.kts) alone.
    patterns: list[str] = [
        *_SCRIPT_PATTERNS,
        "gradle.lockfile",
        _CATALOG_PATTERN,
    ]

    def check(self, index: FileIndex, root: str) -> list[Finding]:
        findings: list[Finding] = []

        for dir_path in sorted(index.keys()):
            files = index[dir_path]

            for fname in sorted(files):
                if fnmatch.fnmatch(fname, _CATALOG_PATTERN):
                    full_path = os.path.join(dir_path, fname)
                    findings.extend(
                        _catalog_findings(
                            full_path, os.path.relpath(full_path, root), self.name
                        )
                    )

            build_files = [f for f in _BUILD_FILES if f in files]
            scripts = sorted(
                f for f in files if any(fnmatch.fnmatch(f, p) for p in _SCRIPT_PATTERNS)
            )
            has_lockfile = "gradle.lockfile" in files

            if not scripts:
                # Only lockfile or nothing — no action
                continue

            # Check for missing lockfile
            if build_files and not has_lockfile:
                # Report against the first build file found
                first_build = build_files[0]
                rel_path = os.path.relpath(os.path.join(dir_path, first_build), root)
                findings.append(
                    Finding(
                        checker=self.name,
                        path=rel_path,
                        line=0,
                        message="{} has no gradle.lockfile; run 'gradle dependencies --write-locks'".format(
                            first_build
                        ),
                        integrity=True,
                    )
                )

            # Parse lockfile coords once (if present) for cross-referencing
            lock_coords: set[str] = set()
            if has_lockfile:
                lock_path = os.path.join(dir_path, "gradle.lockfile")
                lock_coords = _parse_lockfile_coords(lock_path)

            # Check dependency versions in each script
            missing_from_lock: list[str] = []
            for fname in scripts:
                full_path = os.path.join(dir_path, fname)
                rel_path = os.path.relpath(full_path, root)

                try:
                    with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                        lines = fh.readlines()
                except OSError:
                    continue

                for lineno, group, artifact, version in _declarations(lines):
                    coord = "{}:{}".format(group, artifact)

                    if version is None:
                        findings.append(
                            Finding(
                                checker=self.name,
                                path=rel_path,
                                line=lineno,
                                message="'{}' has no version specified; pin to an exact version".format(
                                    coord
                                ),
                            )
                        )
                        continue

                    reason = _is_bad_version(version)
                    if reason:
                        findings.append(
                            Finding(
                                checker=self.name,
                                path=rel_path,
                                line=lineno,
                                message="'{}' uses {}; pin to an exact version".format(
                                    coord, reason
                                ),
                            )
                        )
                        continue

                    # Version is pinned and valid: collect for cross-reference
                    if has_lockfile and coord not in lock_coords:
                        missing_from_lock.append(coord)

            if missing_from_lock:
                lock_rel = os.path.relpath(
                    os.path.join(dir_path, "gradle.lockfile"), root
                )
                findings.append(
                    Finding(
                        checker=self.name,
                        path=lock_rel,
                        line=0,
                        message="gradle.lockfile is stale: missing {} ({})".format(
                            "{} dependency".format(len(missing_from_lock))
                            if len(missing_from_lock) == 1
                            else "{} dependencies".format(len(missing_from_lock)),
                            ", ".join(sorted(missing_from_lock)),
                        ),
                        integrity=True,
                    )
                )

        return findings
