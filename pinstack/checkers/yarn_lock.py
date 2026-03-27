"""yarn.lock checker: warns when package entries are missing an integrity line."""

from __future__ import annotations

import os
from typing import Optional

from pinstack.core import Checker, Finding, FileIndex


def _parse_yarn_lock(lines: list[str]) -> list[tuple]:
    """
    Parse a yarn.lock file (v1 format) and return a list of
    (header_text, start_lineno, has_integrity) tuples — one per package block.
    """
    blocks: list[tuple] = []

    current_header: Optional[str] = None
    current_start = 0
    current_has_integrity = False

    for lineno, raw in enumerate(lines, start=1):
        line = raw.rstrip("\n")

        # Skip blank lines and comments when NOT inside a block
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            if current_header is not None:
                # blank line ends the current block
                blocks.append((current_header, current_start, current_has_integrity))
                current_header = None
                current_has_integrity = False
            continue

        # Non-indented line ending with ":" starts a new package block
        if not line[0:1].isspace() and stripped.endswith(":"):
            # Close any open block first (shouldn't happen for well-formed files, but be safe)
            if current_header is not None:
                blocks.append((current_header, current_start, current_has_integrity))
                current_has_integrity = False
            header_name = stripped[:-1]  # strip trailing ":"
            # Skip Yarn Berry __metadata block — it is not a package entry
            if header_name == "__metadata":
                current_header = None
            else:
                current_header = header_name
                current_start = lineno
        elif current_header is not None:
            # Indented line — field inside a block
            # Accept both Yarn classic "integrity" and Yarn Berry "checksum:"
            if stripped.startswith("integrity ") or stripped.startswith("checksum:"):
                current_has_integrity = True

    # Flush any open block at EOF
    if current_header is not None:
        blocks.append((current_header, current_start, current_has_integrity))

    return blocks


class YarnLockChecker(Checker):
    name = "yarn_lock"
    description = "Checks yarn.lock files for package entries missing integrity hashes"
    patterns: list[str] = ["yarn.lock"]

    def check(self, index: FileIndex, root: str) -> list[Finding]:
        findings: list[Finding] = []

        for dir_path in sorted(index.keys()):
            for fname in sorted(index[dir_path]):
                if fname != "yarn.lock":
                    continue

                full_path = os.path.join(dir_path, fname)
                rel_path = os.path.relpath(full_path, root)

                try:
                    with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                        lines = fh.readlines()
                except OSError:
                    continue

                blocks = _parse_yarn_lock(lines)

                for header, start_lineno, has_integrity in blocks:
                    if not has_integrity:
                        findings.append(
                            Finding(
                                checker=self.name,
                                path=rel_path,
                                line=start_lineno,
                                message=(
                                    "'{}' is missing an integrity hash in yarn.lock".format(
                                        header
                                    )
                                ),
                                integrity=True,
                            )
                        )

        return findings
