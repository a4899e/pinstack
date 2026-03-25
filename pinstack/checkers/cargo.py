"""Cargo checker: enforces checksum presence in Cargo.lock for registry packages."""

import fnmatch
import os
from typing import Dict, List, Optional, Set

from pinstack.core import Checker, Finding, Severity

FileIndex = Dict[str, Set[str]]


class CargoChecker(Checker):
    name = "cargo"
    description = "Checks Cargo.lock for missing checksums on registry packages"
    patterns = ["Cargo.lock"]  # type: List[str]

    def check(self, index, root):
        # type: (FileIndex, str) -> List[Finding]
        findings = []  # type: List[Finding]

        for dir_path in sorted(index.keys()):
            for fname in sorted(index[dir_path]):
                if not fnmatch.fnmatch(fname, "Cargo.lock"):
                    continue

                full_path = os.path.join(dir_path, fname)
                rel_path = os.path.relpath(full_path, root)

                try:
                    with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                        lines = fh.readlines()
                except OSError:
                    continue

                # Parse [[package]] blocks
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
                            severity=Severity.WARNING,
                            message="package '{}' from registry is missing checksum".format(pkg_name),
                        ))

                for lineno, raw_line in enumerate(lines, start=1):
                    line = raw_line.strip()

                    if line == "[[package]]":
                        flush_package(findings, rel_path)
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
                flush_package(findings, rel_path)

        return findings
