"""pnpm-lock.yaml checker: warns when package entries are missing an integrity hash."""

import os
from typing import Dict, Set

from pinstack.core import Checker, Finding

FileIndex = Dict[str, Set[str]]


def _parse_pnpm_lock(lines):
    # type: (List[str]) -> List[tuple]
    """
    Parse a pnpm-lock.yaml file and return a list of
    (package_key, start_lineno, has_integrity) tuples for each package entry.

    We look for the top-level "packages:" section, then treat every
    indented line that ends with ":" as a package entry header.  Within
    each entry we look for a "resolution:" line that contains "integrity".
    """
    blocks = []  # type: List[tuple]

    in_packages_section = False
    current_pkg = None   # type: Optional[str]
    current_start = 0
    current_has_integrity = False

    for lineno, raw in enumerate(lines, start=1):
        line = raw.rstrip("\n")
        stripped = line.strip()

        if not stripped or stripped.startswith("#"):
            continue

        # Detect the "packages:" top-level key
        if line == "packages:":
            in_packages_section = True
            continue

        if not in_packages_section:
            continue

        # A new top-level key (non-indented, not "packages:" itself) ends the section
        if line[0:1] != " " and line[0:1] != "\t":
            if current_pkg is not None:
                blocks.append((current_pkg, current_start, current_has_integrity))
                current_pkg = None
                current_has_integrity = False
            in_packages_section = False
            continue

        # Inside packages section: a line indented by exactly 2 spaces ending with ":"
        # is a package entry header (e.g. "  /express@4.18.2:")
        indent = len(line) - len(line.lstrip(" "))
        if indent == 2 and stripped.endswith(":"):
            # Close previous package block
            if current_pkg is not None:
                blocks.append((current_pkg, current_start, current_has_integrity))
                current_has_integrity = False
            current_pkg = stripped[:-1]  # strip trailing ":"
            current_start = lineno
        elif current_pkg is not None and indent > 2:
            # Field inside the current package block
            # integrity shows up in: resolution: {integrity: sha512-...}
            if "integrity" in stripped:
                current_has_integrity = True

    # Flush any open block
    if current_pkg is not None:
        blocks.append((current_pkg, current_start, current_has_integrity))

    return blocks


class PnpmLockChecker(Checker):
    name = "pnpm_lock"
    description = "Checks pnpm-lock.yaml files for package entries missing integrity hashes"
    patterns = ["pnpm-lock.yaml"]  # type: List[str]

    def check(self, index, root):
        # type: (FileIndex, str) -> List[Finding]
        findings = []  # type: List[Finding]

        for dir_path in sorted(index.keys()):
            for fname in sorted(index[dir_path]):
                if fname != "pnpm-lock.yaml":
                    continue

                full_path = os.path.join(dir_path, fname)
                rel_path = os.path.relpath(full_path, root)

                try:
                    with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                        lines = fh.readlines()
                except OSError:
                    continue

                blocks = _parse_pnpm_lock(lines)

                for pkg_key, start_lineno, has_integrity in blocks:
                    if not has_integrity:
                        findings.append(Finding(
                            checker=self.name,
                            path=rel_path,
                            line=start_lineno,
                            message=(
                                "'{}' is missing an integrity hash in pnpm-lock.yaml".format(pkg_key)
                            ),
                        ))

        return findings
