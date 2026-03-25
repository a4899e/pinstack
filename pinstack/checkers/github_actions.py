"""GitHub Actions checker: enforces SHA pinning on action refs."""

import fnmatch
import os
import re
from typing import Dict, List, Set

from pinstack.core import Checker, Finding

FileIndex = Dict[str, Set[str]]

# Matches lines containing uses: <ref>
_USES_RE = re.compile(r'^\s*-?\s*uses:\s*(\S+)')

# A 40-character lowercase hex SHA
_SHA_RE = re.compile(r'^[0-9a-f]{40}$')

# Workflow files live under .github/workflows/ (any depth above that)
_WORKFLOW_PATH_PARTS = (".github", "workflows")


def _is_workflow_path(rel_path):
    # type: (str) -> bool
    """Return True if rel_path contains .github/workflows/ as consecutive path components."""
    parts = rel_path.replace("\\", "/").split("/")
    for i in range(len(parts) - 1):
        if parts[i] == ".github" and parts[i + 1] == "workflows":
            return True
    return False


class GitHubActionsChecker(Checker):
    name = "github_actions"
    description = "Checks GitHub Actions workflow files for SHA-pinned action refs"
    patterns = ["*.yml", "*.yaml"]  # type: List[str]

    def check(self, index, root):
        # type: (FileIndex, str) -> List[Finding]
        findings = []  # type: List[Finding]

        for dir_path in sorted(index.keys()):
            for fname in sorted(index[dir_path]):
                if not any(fnmatch.fnmatch(fname, p) for p in self.patterns):
                    continue

                full_path = os.path.join(dir_path, fname)
                rel_path = os.path.relpath(full_path, root)

                # Only check files under .github/workflows/
                if not _is_workflow_path(rel_path):
                    continue

                try:
                    with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                        lines = fh.readlines()
                except OSError:
                    continue

                for lineno, raw_line in enumerate(lines, start=1):
                    m = _USES_RE.match(raw_line)
                    if not m:
                        continue

                    ref = m.group(1)

                    # Skip local actions
                    if ref.startswith("./"):
                        continue

                    # Skip docker:// refs
                    if ref.startswith("docker://"):
                        continue

                    # Check for @ separator
                    if "@" not in ref:
                        findings.append(Finding(
                            checker=self.name,
                            path=rel_path,
                            line=lineno,
                            message="action '{}' has no @ ref; pin to a full-length SHA".format(ref),
                        ))
                        continue

                    action_part, pin = ref.rsplit("@", 1)
                    if not _SHA_RE.match(pin):
                        findings.append(Finding(
                            checker=self.name,
                            path=rel_path,
                            line=lineno,
                            message="action '{}' is not pinned to a full-length SHA (got '{}')".format(
                                action_part, pin
                            ),
                        ))

        return findings
