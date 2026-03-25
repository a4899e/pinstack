"""Docker Compose checker: enforces digest pinning on image references."""

from __future__ import annotations

import fnmatch
import os
import re

from pinstack.core import Checker, Finding, FileIndex

# Matches lines like:   image: nginx:1.25
_IMAGE_RE = re.compile(r'^\s*image:\s*(\S+)')


class ComposeChecker(Checker):
    name = "compose"
    description = "Checks docker-compose files for digest (@sha256:) pinning on image references"
    patterns: list[str] = [
        "docker-compose*.yml",
        "docker-compose*.yaml",
        "compose*.yml",
        "compose*.yaml",
    ]

    def check(self, index: FileIndex, root: str) -> list[Finding]:
        findings: list[Finding] = []

        for dir_path in sorted(index.keys()):
            for fname in sorted(index[dir_path]):
                if not any(fnmatch.fnmatch(fname, p) for p in self.patterns):
                    continue

                full_path = os.path.join(dir_path, fname)
                rel_path = os.path.relpath(full_path, root)

                try:
                    with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                        lines = fh.readlines()
                except OSError:
                    continue

                for lineno, raw_line in enumerate(lines, start=1):
                    m = _IMAGE_RE.match(raw_line)
                    if not m:
                        continue

                    image = m.group(1)

                    if "@sha256:" not in image:
                        findings.append(Finding(
                            checker=self.name,
                            path=rel_path,
                            line=lineno,
                            message="image '{}' is not pinned with @sha256: digest".format(image),
                        ))

        return findings
