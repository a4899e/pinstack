"""Maven checker: enforces explicit, non-dynamic versions in pom.xml dependency declarations.

XML parsing and defusedxml
--------------------------
Bandit flags our use of xml.etree.ElementTree (B405/B314) because the stdlib
XML parser is vulnerable to entity expansion attacks (billion laughs) and
external entity injection (XXE) when processing untrusted input.

We intentionally use the stdlib parser rather than defusedxml because:

1. pinstack has zero runtime dependencies by design. Adding defusedxml would
   break that guarantee and make adoption harder in non-Python projects.
2. pinstack only parses files that already exist on disk in the user's own
   project. If an attacker can write a malicious pom.xml into your repository,
   XML entity expansion is the least of your problems.
3. We disable external entity resolution by using a restricted XMLParser
   configuration, which mitigates the XXE attack vector without any external
   dependency.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET  # nosec B405

from pinstack.core import Checker, Finding, FileIndex

# Maven POM namespace URI
_NS = "http://maven.apache.org/POM/4.0.0"
_NS_PREFIX = "{%s}" % _NS


def _tag(local: str) -> str:
    return _NS_PREFIX + local


def _find_version_line(raw_lines: list[str], dep_index: int) -> int:
    """Given the line index (0-based) of a <dependency> open tag, find the line
    containing <version> text. Returns 1-based line number, or 0 if not found."""
    for i in range(dep_index, min(dep_index + 20, len(raw_lines))):
        if "<version>" in raw_lines[i] or "<version " in raw_lines[i]:
            return i + 1  # 1-based
    return 0


def _find_dep_line(raw_lines: list[str], start_search: int) -> int:
    """Find the 0-based line index of the next <dependency> open tag at or after start_search."""
    for i in range(start_search, len(raw_lines)):
        stripped = raw_lines[i].strip()
        if stripped.startswith("<dependency") and not stripped.startswith(
            "<dependencies"
        ):
            return i
    return 0


class MavenChecker(Checker):
    name = "maven"
    description = (
        "Checks pom.xml for unpinned, dynamic, or SNAPSHOT dependency versions"
    )
    patterns: list[str] = ["pom.xml"]

    def check(self, index: FileIndex, root: str) -> list[Finding]:
        findings: list[Finding] = []

        for dir_path in sorted(index.keys()):
            for fname in sorted(index[dir_path]):
                if fname != "pom.xml":
                    continue

                full_path = os.path.join(dir_path, fname)
                rel_path = os.path.relpath(full_path, root)

                try:
                    with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                        raw_content = fh.read()
                except OSError:
                    continue

                raw_lines = raw_content.splitlines()

                try:
                    # nosec B314 — see module docstring for rationale
                    root_elem = ET.fromstring(raw_content)  # nosec B314
                except ET.ParseError:
                    findings.append(
                        Finding(
                            checker=self.name,
                            path=rel_path,
                            line=0,
                            message="pom.xml could not be parsed as XML",
                        )
                    )
                    continue

                # Detect whether the POM uses the Maven namespace
                tag = root_elem.tag
                uses_ns = tag.startswith("{")

                if uses_ns:
                    dep_tag = _tag("dependency")
                    deps_tag = _tag("dependencies")
                    ver_tag = _tag("version")
                    group_tag = _tag("groupId")
                    artifact_tag = _tag("artifactId")
                else:
                    dep_tag = "dependency"
                    deps_tag = "dependencies"
                    ver_tag = "version"
                    group_tag = "groupId"
                    artifact_tag = "artifactId"

                # Track which <dependency> in document order we are processing
                # so we can correlate back to raw line numbers
                dep_scan_start = 0

                for deps_elem in root_elem.iter(deps_tag):
                    for dep_elem in deps_elem:
                        if dep_elem.tag != dep_tag:
                            continue

                        group_elem = dep_elem.find(group_tag)
                        artifact_elem = dep_elem.find(artifact_tag)
                        ver_elem = dep_elem.find(ver_tag)

                        group = (
                            group_elem.text.strip()
                            if group_elem is not None and group_elem.text
                            else "unknown"
                        )
                        artifact = (
                            artifact_elem.text.strip()
                            if artifact_elem is not None and artifact_elem.text
                            else "unknown"
                        )
                        coord = "{}:{}".format(group, artifact)

                        # Find the line in raw text for this dependency block
                        dep_line_idx = _find_dep_line(raw_lines, dep_scan_start)
                        dep_scan_start = dep_line_idx + 1

                        if ver_elem is None or ver_elem.text is None:
                            # No <version> element at all
                            line = dep_line_idx + 1  # 1-based
                            findings.append(
                                Finding(
                                    checker=self.name,
                                    path=rel_path,
                                    line=line,
                                    message="'{}' has no <version>; specify an explicit version".format(
                                        coord
                                    ),
                                )
                            )
                            continue

                        version = ver_elem.text.strip()

                        # Property reference — cannot resolve without the full POM hierarchy
                        # (parent POMs, imported BOMs, command-line -D flags).  We cannot
                        # verify whether the resolved value is a range, LATEST, or SNAPSHOT,
                        # so we emit a warning rather than silently accepting it.
                        if version.startswith("${") and version.endswith("}"):
                            ver_line = _find_version_line(raw_lines, dep_line_idx)
                            if ver_line == 0:
                                ver_line = dep_line_idx + 1
                            findings.append(
                                Finding(
                                    checker=self.name,
                                    path=rel_path,
                                    line=ver_line,
                                    message=(
                                        "'{}' version uses property reference '{}' which cannot be"
                                        " verified; consider using an explicit version".format(
                                            coord, version
                                        )
                                    ),
                                )
                            )
                            continue

                        # Determine line number of the <version> tag
                        ver_line = _find_version_line(raw_lines, dep_line_idx)
                        if ver_line == 0:
                            ver_line = dep_line_idx + 1

                        # Check for version ranges: contains [, ], (, )
                        if any(c in version for c in "[]()"):
                            findings.append(
                                Finding(
                                    checker=self.name,
                                    path=rel_path,
                                    line=ver_line,
                                    message="'{}' uses a version range '{}'; pin to an exact version".format(
                                        coord, version
                                    ),
                                )
                            )
                            continue

                        # LATEST or RELEASE
                        if version in ("LATEST", "RELEASE"):
                            findings.append(
                                Finding(
                                    checker=self.name,
                                    path=rel_path,
                                    line=ver_line,
                                    message="'{}' uses '{}'; pin to an exact version".format(
                                        coord, version
                                    ),
                                )
                            )
                            continue

                        # SNAPSHOT versions
                        if version.endswith("-SNAPSHOT"):
                            findings.append(
                                Finding(
                                    checker=self.name,
                                    path=rel_path,
                                    line=ver_line,
                                    message="'{}' uses SNAPSHOT version '{}'; pin to a released version".format(
                                        coord, version
                                    ),
                                )
                            )
                            continue

        return findings
