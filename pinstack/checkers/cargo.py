"""Cargo checker: enforces checksum presence in Cargo.lock for registry packages."""

import fnmatch
import os
import re
from typing import Dict, List, Optional, Set

from pinstack.core import Checker, Finding

FileIndex = Dict[str, Set[str]]

# Sections in Cargo.toml that list dependencies
_DEP_SECTION_NAMES = frozenset([
    "dependencies",
    "dev-dependencies",
    "build-dependencies",
])


def _parse_cargo_toml_deps(path):
    # type: (str) -> List[str]
    """Return list of dependency names from Cargo.toml."""
    deps = []  # type: List[str]
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return deps

    in_dep_section = False
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # Section header
        if line.startswith("["):
            # Strip table-array brackets and inline-table notation
            section = line.strip("[]").strip()
            # Handle [workspace.dependencies] etc. by taking the last component
            last_part = section.split(".")[-1]
            in_dep_section = last_part in _DEP_SECTION_NAMES
            continue
        if not in_dep_section:
            continue
        # Dependency line: "name = ..." or "name = { ... }"
        m = re.match(r'^([A-Za-z0-9_\-]+)\s*=', line)
        if m:
            deps.append(m.group(1))

    return deps


def _parse_cargo_lock_names(lines):
    # type: (List[str]) -> Set[str]
    """Return set of package names from already-read Cargo.lock lines."""
    names = set()  # type: Set[str]
    for raw_line in lines:
        line = raw_line.strip()
        if line.startswith("name = "):
            names.add(line[len("name = "):].strip().strip('"'))
    return names


class CargoChecker(Checker):
    name = "cargo"
    description = "Checks Cargo.lock for missing checksums on registry packages"
    patterns = ["Cargo.lock", "Cargo.toml"]  # type: List[str]

    def check(self, index, root):
        # type: (FileIndex, str) -> List[Finding]
        findings = []  # type: List[Finding]

        for dir_path in sorted(index.keys()):
            files = index[dir_path]

            if "Cargo.lock" not in files:
                continue

            lock_path = os.path.join(dir_path, "Cargo.lock")
            rel_lock = os.path.relpath(lock_path, root)

            try:
                with open(lock_path, "r", encoding="utf-8", errors="replace") as fh:
                    lines = fh.readlines()
            except OSError:
                continue

            # Parse [[package]] blocks for checksum checks
            in_package = False
            pkg_name = None        # type: Optional[str]
            pkg_line = 0
            has_source = False
            has_checksum = False

            def flush_package(findings_list, rp):
                # type: (List[Finding], str) -> None
                if in_package and has_source and not has_checksum and pkg_name is not None:
                    findings_list.append(Finding(
                        checker="cargo",
                        path=rp,
                        line=pkg_line,
                        message="package '{}' from registry is missing checksum".format(pkg_name),
                    ))

            for lineno, raw_line in enumerate(lines, start=1):
                line = raw_line.strip()

                if line == "[[package]]":
                    flush_package(findings, rel_lock)
                    in_package = True
                    pkg_name = None
                    pkg_line = lineno
                    has_source = False
                    has_checksum = False
                    continue

                if not in_package:
                    continue

                if line.startswith("name = "):
                    pkg_name = line[len("name = "):].strip().strip('"')
                elif line.startswith("source = "):
                    has_source = True
                elif line.startswith("checksum = "):
                    has_checksum = True

            # Flush last package
            flush_package(findings, rel_lock)

            # Cross-reference: every Cargo.toml dep must appear in Cargo.lock
            if "Cargo.toml" in files:
                toml_path = os.path.join(dir_path, "Cargo.toml")
                rel_toml = os.path.relpath(toml_path, root)
                toml_deps = _parse_cargo_toml_deps(toml_path)
                lock_names = _parse_cargo_lock_names(lines)

                missing = [dep for dep in toml_deps if dep not in lock_names]
                if missing:
                    findings.append(Finding(
                        checker=self.name,
                        path=rel_lock,
                        line=0,
                        message="Cargo.lock is stale: missing {} ({})".format(
                            "{} dependency".format(len(missing)) if len(missing) == 1 else "{} dependencies".format(len(missing)),
                            ", ".join(sorted(missing)),
                        ),
                    ))

        return findings
