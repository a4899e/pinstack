"""Dockerfile checker: enforces digest pinning on FROM image references."""

from __future__ import annotations

import fnmatch
import os
import re

from pinstack.core import Checker, Finding, FileIndex

# Matches: FROM <image> [AS <name>]
# Group 1: image reference
_FROM_RE = re.compile(r'^FROM\s+(\S+)(?:\s+AS\s+\S+)?\s*$', re.IGNORECASE)


def _is_build_stage_alias(image: str) -> bool:
    """Return True if image looks like a bare stage alias (no :, @, ., /)."""
    return not any(c in image for c in (':', '@', '.', '/'))


class DockerfileChecker(Checker):
    name = "dockerfile"
    description = "Checks Dockerfile FROM instructions for digest (@sha256:) pinning"
    patterns: list[str] = ["Dockerfile*"]

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
                    line = raw_line.strip()
                    m = _FROM_RE.match(line)
                    if not m:
                        continue

                    image = m.group(1)

                    # Skip FROM scratch
                    if image.lower() == "scratch":
                        continue

                    # Skip build stage aliases (bare word: no :, @, ., /)
                    if _is_build_stage_alias(image):
                        continue

                    # Require @sha256:
                    if "@sha256:" not in image:
                        findings.append(Finding(
                            checker=self.name,
                            path=rel_path,
                            line=lineno,
                            message="FROM '{}' is not pinned with @sha256: digest".format(image),
                            integrity=True,
                        ))

        return findings
