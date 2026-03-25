"""Go checker: enforces go.sum exists alongside go.mod and checks h1: hashes."""

from __future__ import annotations

import os
import re

from pinstack.core import Checker, Finding, FileIndex


def _parse_gomod_deps(path: str) -> list[str]:
    """Return list of module names from require directives in go.mod."""
    deps: list[str] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return deps

    in_block = False
    for raw_line in lines:
        line = raw_line.strip()
        if line.startswith("//"):
            continue
        if in_block:
            if line == ")":
                in_block = False
                continue
            # Lines inside require(...) block: "module version [// indirect]"
            parts = line.split()
            if parts and not parts[0].startswith("//"):
                deps.append(parts[0])
        else:
            if line == "require (":
                in_block = True
                continue
            # Single-line: "require module version"
            m = re.match(r'^require\s+(\S+)\s+', line)
            if m:
                deps.append(m.group(1))

    return deps


def _parse_gosum_modules(path: str) -> set[str]:
    """Return set of module names present in go.sum (ignoring /go.mod suffix)."""
    modules: set[str] = set()
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return modules

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if parts:
            # Strip /go.mod suffix from the module name field (first field)
            module = parts[0]
            modules.add(module)

    return modules


class GoChecker(Checker):
    name = "go"
    description = "Checks go.mod/go.sum pairs: go.sum must exist and contain h1: hashes"
    patterns: list[str] = ["go.mod", "go.sum"]

    def check(self, index: FileIndex, root: str) -> list[Finding]:
        findings: list[Finding] = []

        for dir_path in sorted(index.keys()):
            files = index[dir_path]
            has_mod = "go.mod" in files
            has_sum = "go.sum" in files

            if not has_mod:
                # Only go.sum present — no action needed
                continue

            if not has_sum:
                rel_path = os.path.relpath(os.path.join(dir_path, "go.mod"), root)
                findings.append(Finding(
                    checker=self.name,
                    path=rel_path,
                    line=0,
                    message="go.mod has no corresponding go.sum; run 'go mod tidy'",
                ))
                continue

            # Both exist — check go.sum for h1: hashes
            sum_path = os.path.join(dir_path, "go.sum")
            mod_path = os.path.join(dir_path, "go.mod")
            rel_sum = os.path.relpath(sum_path, root)

            try:
                with open(sum_path, "r", encoding="utf-8", errors="replace") as fh:
                    lines = fh.readlines()
            except OSError:
                continue

            for lineno, raw_line in enumerate(lines, start=1):
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue

                parts = line.split()
                # go.sum format: <module> <version> <hash>
                if len(parts) < 3:
                    continue

                hash_field = parts[2]
                if not hash_field.startswith("h1:"):
                    findings.append(Finding(
                        checker=self.name,
                        path=rel_sum,
                        line=lineno,
                        message="go.sum entry '{}' is missing h1: hash".format(parts[0]),
                    ))

            # Cross-reference: every go.mod dep must appear in go.sum
            gomod_deps = _parse_gomod_deps(mod_path)
            gosum_modules = _parse_gosum_modules(sum_path)

            missing: list[str] = []
            for dep in gomod_deps:
                if dep not in gosum_modules and (dep + "/go.mod") not in gosum_modules:
                    missing.append(dep)
            if missing:
                findings.append(Finding(
                    checker=self.name,
                    path=rel_sum,
                    line=0,
                    message="go.sum is stale: missing {} ({})".format(
                        "{} dependency".format(len(missing)) if len(missing) == 1 else "{} dependencies".format(len(missing)),
                        ", ".join(sorted(missing)),
                    ),
                ))

        return findings
