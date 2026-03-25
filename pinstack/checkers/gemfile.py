"""Gemfile checker: warns when Gemfile exists but Gemfile.lock does not."""

import os
from typing import Dict, List, Set

from pinstack.core import Checker, Finding

FileIndex = Dict[str, Set[str]]


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

        return findings
