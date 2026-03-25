"""Gemfile checker: warns when Gemfile exists but Gemfile.lock does not."""

import os
import re
from typing import Dict, List, Set

from pinstack.core import Checker, Finding

FileIndex = Dict[str, Set[str]]


def _parse_gemfile_deps(path):
    # type: (str) -> List[str]
    """Return list of gem names from Gemfile gem(...) declarations."""
    deps = []  # type: List[str]
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return deps

    for raw_line in lines:
        line = raw_line.strip()
        if line.startswith("#"):
            continue
        # Match: gem "name" or gem 'name' optionally followed by version/options
        m = re.match(r"""^gem\s+['"]([^'"]+)['"]""", line)
        if m:
            deps.append(m.group(1))

    return deps


def _parse_gemfile_lock_specs(path):
    # type: (str) -> Set[str]
    """Return set of gem names from the specs: section of Gemfile.lock."""
    names = set()  # type: Set[str]
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return names

    in_specs = False
    for raw_line in lines:
        # Detect "  specs:" (indented)
        stripped = raw_line.rstrip("\n")
        if stripped.rstrip() == "  specs:":
            in_specs = True
            continue
        if in_specs:
            # Empty line or next section header ends specs block
            if not stripped.strip():
                in_specs = False
                continue
            # Gem lines are indented with 4 spaces: "    name (version)"
            m = re.match(r'^    ([A-Za-z0-9_\-\.]+)\s+\(', stripped)
            if m:
                names.add(m.group(1))
            elif stripped and not stripped.startswith(" "):
                # Non-indented line — new section
                in_specs = False

    return names


class GemfileChecker(Checker):
    name = "gemfile"
    description = "Checks that every Gemfile has a corresponding Gemfile.lock"
    patterns = ["Gemfile", "Gemfile.lock"]  # type: List[str]

    def check(self, index, root):
        # type: (FileIndex, str) -> List[Finding]
        findings = []  # type: List[Finding]

        for dir_path in sorted(index.keys()):
            files = index[dir_path]
            has_gemfile = "Gemfile" in files
            has_lock = "Gemfile.lock" in files

            if has_gemfile and not has_lock:
                rel_path = os.path.relpath(os.path.join(dir_path, "Gemfile"), root)
                findings.append(Finding(
                    checker=self.name,
                    path=rel_path,
                    line=0,
                    message="Gemfile has no corresponding Gemfile.lock; run 'bundle install'",
                ))
                continue

            # Both exist — cross-reference deps
            if has_gemfile and has_lock:
                gemfile_path = os.path.join(dir_path, "Gemfile")
                lock_path = os.path.join(dir_path, "Gemfile.lock")
                rel_gemfile = os.path.relpath(gemfile_path, root)

                gemfile_deps = _parse_gemfile_deps(gemfile_path)
                lock_specs = _parse_gemfile_lock_specs(lock_path)

                missing = [dep for dep in gemfile_deps if dep not in lock_specs]
                if missing:
                    rel_lock = os.path.relpath(lock_path, root)
                    findings.append(Finding(
                        checker=self.name,
                        path=rel_lock,
                        line=0,
                        message="Gemfile.lock is stale: missing {} ({})".format(
                            "{} dependency".format(len(missing)) if len(missing) == 1 else "{} dependencies".format(len(missing)),
                            ", ".join(sorted(missing)),
                        ),
                    ))

        return findings
