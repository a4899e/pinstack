"""Gradle checker: enforces pinned versions in build.gradle / build.gradle.kts files
and requires a gradle.lockfile companion."""

from __future__ import annotations

import fnmatch
import os
import re
from typing import Optional

from pinstack.core import Checker, Finding, FileIndex, _matches_any_pattern

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
    r"""\b(?P<key>group|name|version)\s*[:=]\s*"""
    r"""(?:['"](?P<value>[^'"]*)['"]|(?P<expr>[A-Za-z_$][\w.$]*(?:\(\))?))"""
)

# Patterns that indicate a dynamic/bad version in the version segment
_RANGE_RE = re.compile(r"[\[\]()]")


def _match_map_notation(line: str) -> Optional[tuple[str, str, Optional[str]]]:
    """Return (group, name, version or None) for a map-notation declaration."""
    if not _CONFIG_START_RE.match(line):
        return None
    fields: dict[str, str] = {}
    for m in _MAP_FIELD_RE.finditer(line):
        value = m.group("value")
        if value is None:
            # An unquoted expression (version: guavaVersion, or Kotlin
            # version = libs.versions.x.get()) is resolved at build time.
            expr = m.group("expr")
            value = expr if expr.startswith("$") else "$" + expr
        fields.setdefault(m.group("key"), value)
    if not fields.get("group") or not fields.get("name"):
        return None
    return fields["group"], fields["name"], fields.get("version")


def _strip_comments(text: str) -> str:
    """Blank ``//`` and ``/* */`` comments outside string literals.

    Newlines are kept so line numbers still hold. Groovy and Kotlin strings
    may be single-, double- or triple-quoted.
    """
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        two = text[i : i + 2]
        if two == "//":
            end = text.find("\n", i)
            end = n if end == -1 else end
            out.append(" " * (end - i))
            i = end
        elif two == "/*":
            end = text.find("*/", i + 2)
            end = n if end == -1 else end + 2
            out.append("".join(c if c == "\n" else " " for c in text[i:end]))
            i = end
        elif text[i] in ("'", '"'):
            quote = text[i] * 3 if text.startswith(text[i] * 3, i) else text[i]
            j = i + len(quote)
            while j < n and not text.startswith(quote, j):
                if text[j] == "\n" and len(quote) == 1:
                    break  # unterminated single-line string
                j += 2 if text[j] == "\\" else 1
            end = min(j + len(quote), n)
            out.append(text[i:end])
            i = end
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


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


# Further coordinates in Groovy's list form: implementation 'g:a:1', 'g:b:2'
_EXTRA_COORD_RE = re.compile(
    r""",\s*['"](?P<group>[^'":\s]+):(?P<artifact>[^'":\s]+)(?::(?P<version>[^'"]*))?['"]"""
)


def _declarations(
    lines: list[str],
) -> list[tuple[int, str, str, str, Optional[str]]]:
    """Return (line, config, group, artifact, version or None) per declaration.

    Comments are removed first, so a commented-out declaration is ignored
    even when it follows a brace or semicolon.
    """
    found: list[tuple[int, str, str, str, Optional[str]]] = []
    code_lines = _strip_comments("".join(lines)).split("\n")
    for lineno, code in enumerate(code_lines, start=1):
        for statement in _split_statements(code):
            m = _DEP_RE.match(statement)
            if m:
                config = m.group("config")
                found.append(
                    (
                        lineno,
                        config,
                        m.group("group") or "",
                        m.group("artifact") or "",
                        m.group("version"),
                    )
                )
                for extra in _EXTRA_COORD_RE.finditer(statement, m.end()):
                    found.append(
                        (
                            lineno,
                            config,
                            extra.group("group"),
                            extra.group("artifact"),
                            extra.group("version"),
                        )
                    )
                continue
            parsed = _match_map_notation(statement)
            if parsed is not None:
                config_match = _CONFIG_START_RE.match(statement)
                config = config_match.group("config") if config_match else ""
                found.append((lineno, config) + parsed)
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
_VERSION_FIELD_RE = re.compile(
    r"""\b(version|strictly|require|prefer)\s*=\s*["']([^"']*)["']"""
)
# version.ref = "x", or the same written as an inline table: version = { ref = "x" }
_VERSION_REF_RE = re.compile(r"""\bversion\s*(?:\.\s*ref\s*=|=\s*\{[^}]*\bref\s*=)""")
_CATALOG_SECTIONS = ("versions", "libraries", "plugins")


def _catalog_entry_versions(section: str, value: str) -> Optional[list[str]]:
    """Return the version strings a catalog entry declares.

    Returns None when the entry takes its version from ``[versions]``
    (checked there) or is not an entry this checker reads.
    """
    string_match = _TOML_STRING_RE.match(value)
    if string_match:
        text = string_match.group(1)
        if section == "versions":
            return [text]
        if section == "libraries":
            # "group:artifact:version"
            parts = text.split(":")
            return [parts[2] if len(parts) >= 3 else ""]
        # plugins: "id:version"
        return [text.split(":", 1)[1] if ":" in text else ""]
    if value.startswith("{") or "\n" in value:
        if _VERSION_REF_RE.search(value):
            return None
        versions = [v for _, v in _VERSION_FIELD_RE.findall(value)]
        return versions or [""]
    return None


def _catalog_findings(path: str, rel_path: str, checker: str) -> list[Finding]:
    """Check a Gradle version catalog (libs.versions.toml).

    ``[versions]`` entries, and the versions written inline in ``[libraries]``
    and ``[plugins]``, must be exact. Entries may be written inline
    (``guava = { module = ..., version = ... }``) or as their own table
    (``[libraries.guava]``). A library with neither a version nor a version
    reference resolves through a platform or constraint the catalog cannot
    show, so it is reported as missing a version. Rich versions
    (``{ strictly = ..., require = ..., prefer = ... }``) are checked field
    by field.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return []

    # 1. Collect (alias, section, line, value text) for every entry.
    entries: list[tuple[str, str, int, str]] = []
    section = ""
    table_entry: Optional[list] = None  # [alias, section, line, text] for [libraries.x]
    for lineno, raw in enumerate(lines, start=1):
        if raw.lstrip().startswith("#"):
            continue
        m = _CATALOG_SECTION_RE.match(raw)
        if m:
            if table_entry is not None:
                entries.append(tuple(table_entry))  # type: ignore[arg-type]
                table_entry = None
            section = m.group(1).strip()
            parent, _, alias = section.partition(".")
            if parent in _CATALOG_SECTIONS and alias:
                table_entry = [alias.strip("\"'"), parent, lineno, "\n"]
            continue
        if table_entry is not None:
            table_entry[3] += raw
            continue
        if section not in _CATALOG_SECTIONS:
            continue
        m = _CATALOG_ENTRY_RE.match(raw)
        if m:
            entries.append((m.group(1), section, lineno, m.group(2)))
    if table_entry is not None:
        entries.append(tuple(table_entry))  # type: ignore[arg-type]

    # 2. Check each declared version.
    findings: list[Finding] = []
    for alias, entry_section, lineno, value in entries:
        versions = _catalog_entry_versions(entry_section, value)
        for version in versions or []:
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
                    message=message.format(alias, entry_section, reason),
                )
            )
    return findings


_BUILD_FILES = ("build.gradle", "build.gradle.kts")
_BUILDSCRIPT_LOCKFILE = "buildscript-gradle.lockfile"
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
        _BUILDSCRIPT_LOCKFILE,
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
                f for f in files if _matches_any_pattern(f, set(_SCRIPT_PATTERNS))
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
            has_buildscript_lock = _BUILDSCRIPT_LOCKFILE in files
            buildscript_coords: set[str] = set()
            if has_buildscript_lock:
                buildscript_coords = _parse_lockfile_coords(
                    os.path.join(dir_path, _BUILDSCRIPT_LOCKFILE)
                )

            # Check dependency versions in each script. Missing coordinates
            # are kept per lockfile: [gradle.lockfile, buildscript lockfile].
            missing_from_lock: tuple[list[str], list[str]] = ([], [])
            for fname in scripts:
                full_path = os.path.join(dir_path, fname)
                rel_path = os.path.relpath(full_path, root)

                try:
                    with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                        lines = fh.readlines()
                except OSError:
                    continue

                for lineno, config, group, artifact, version in _declarations(lines):
                    coord = "{}:{}".format(group, artifact)
                    # Gradle locks the buildscript classpath separately.
                    if config == "classpath":
                        locked, lock_present = buildscript_coords, has_buildscript_lock
                    else:
                        locked, lock_present = lock_coords, has_lockfile

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
                    if lock_present and coord not in locked:
                        missing_from_lock[config == "classpath"].append(coord)

            for lock_name, missing in zip(
                ("gradle.lockfile", _BUILDSCRIPT_LOCKFILE), missing_from_lock
            ):
                if not missing:
                    continue
                findings.append(
                    Finding(
                        checker=self.name,
                        path=os.path.relpath(os.path.join(dir_path, lock_name), root),
                        line=0,
                        message="{} is stale: missing {} ({})".format(
                            lock_name,
                            "{} dependency".format(len(missing))
                            if len(missing) == 1
                            else "{} dependencies".format(len(missing)),
                            ", ".join(sorted(missing)),
                        ),
                        integrity=True,
                    )
                )

        return findings
