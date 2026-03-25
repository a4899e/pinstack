"""Dockerfile checker: enforces digest pinning on FROM image references."""

import fnmatch
import os
import re
from typing import Dict, List, Set

from pinstack.core import Checker, Finding, Severity

FileIndex = Dict[str, Set[str]]

# Matches: FROM <image> [AS <name>]
# Group 1: image reference
_FROM_RE = re.compile(r'^FROM\s+(\S+)(?:\s+AS\s+\S+)?\s*$', re.IGNORECASE)


def _is_build_stage_alias(image):
    # type: (str) -> bool
    """Return True if image looks like a bare stage alias (no :, @, ., /)."""
    return not any(c in image for c in (':', '@', '.', '/'))


class DockerfileChecker(Checker):
    name = "dockerfile"
    description = "Checks Dockerfile FROM instructions for digest (@sha256:) pinning"
    patterns = ["Dockerfile*"]  # type: List[str]

    def check(self, index, root):
        # type: (FileIndex, str) -> List[Finding]
        findings = []  # type: List[Finding]

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
                            severity=Severity.ERROR,
                            message="FROM '{}' is not pinned with @sha256: digest".format(image),
                        ))

        return findings
