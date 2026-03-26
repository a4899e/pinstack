"""Requirements checker: enforces == pinning and --hash= on requirements*.txt files."""

from __future__ import annotations

import fnmatch
import os
import re

from pinstack.core import Checker, Finding, FileIndex

# Matches a package specifier line.
# Group 1: package name (including optional [extras])
# Group 2: operator (==, >=, ~=, >, <, !=, <=, ~) -- absent means bare name
# Group 3: version string -- absent means bare name
_SPECIFIER_RE = re.compile(
    r'^([A-Za-z0-9_.\-]+(?:\[[A-Za-z0-9_.,\s]+\])?)'  # name + optional extras
    r'\s*'
    r'(==|!=|~=|>=|<=|>|<|~)?'                          # optional operator
    r'\s*'
    r'([^\s;#\\]*)'                                      # optional version
    r'\s*'
    r'(.*)'                                               # remainder (hashes, options, etc.)
)


def _check_line(raw_line: str) -> tuple:
    """
    Parse a single requirements line.

    Returns (skip, has_pin_error, has_hash_warning, package_name) where:
      skip             -- bool: line should be ignored entirely
      has_pin_error   -- bool: missing == pin (ERROR)
      has_hash_warning -- bool: has == pin but missing --hash (WARNING)
      package_name     -- str: the bare package name (for messages)
    """
    # Strip continuation backslash and whitespace
    line = raw_line.strip()
    if line.endswith("\\"):
        line = line[:-1].strip()

    # Skip blank lines
    if not line:
        return True, False, False, ""

    # Skip comments
    if line.startswith("#"):
        return True, False, False, ""

    # Skip pip options: --option, -r (include), -c (constraint), -f, -i, -Z
    # But NOT -e (editable installs) — those are real deps that bypass pinning
    if line.startswith("--") or (line.startswith("-") and not line.startswith("-e")):
        return True, False, False, ""

    # Editable installs (-e) are not content-addressed
    if line.startswith("-e"):
        pkg = line[2:].strip()
        return False, True, False, pkg

    # URL lines are not content-addressed
    if line.startswith("http://") or line.startswith("https://") or line.startswith("git+"):
        return False, True, False, line

    # Strip inline comments (but NOT inside the specifier itself -- hashes are after whitespace)
    # We need the full line to detect --hash= later.
    # Remove only a trailing # comment that is NOT part of a hash
    # Hashes look like:  --hash=sha256:abc123
    # Safe to strip everything after first ' #' that follows whitespace
    full_for_hash = line  # keep original (with possible inline comment stripped for hash check)

    # Extract the package specifier part (everything before the first space or backslash)
    # The specifier is the leading non-whitespace token
    specifier_token = line.split()[0] if line.split() else ""

    m = _SPECIFIER_RE.match(specifier_token)
    if not m:
        # Not recognizable; skip
        return True, False, False, ""

    pkg_name = m.group(1)
    operator = m.group(2) or ""
    version = m.group(3) or ""

    # Determine pin status
    if operator == "==" and version:
        # Correctly pinned. Now check for --hash=
        has_hash = "--hash=" in full_for_hash
        if has_hash:
            return False, False, False, pkg_name  # clean
        else:
            return False, False, True, pkg_name   # missing hash -> WARNING
    else:
        # Missing == pin -> ERROR
        return False, True, False, pkg_name


class RequirementsChecker(Checker):
    name = "requirements"
    description = "Checks requirements*.txt files for == pinning and --hash= integrity markers"
    patterns: list[str] = ["requirements*.txt"]

    def check(self, index: FileIndex, root: str) -> list[Finding]:
        findings: list[Finding] = []

        for dir_path in sorted(index.keys()):
            for fname in sorted(index[dir_path]):
                # Only process files matching our patterns
                if not any(fnmatch.fnmatch(fname, p) for p in self.patterns):
                    continue

                full_path = os.path.join(dir_path, fname)
                rel_path = os.path.relpath(full_path, root)

                try:
                    with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                        raw_lines = fh.readlines()
                except OSError:
                    continue

                # We need to handle continuation lines: a line ending with \ means the next
                # line is a continuation (typically holds --hash= entries). We join logical lines
                # so that hash detection works correctly.
                logical_lines: list[tuple] = []  # (logical_text, start_lineno)
                i = 0
                while i < len(raw_lines):
                    lineno = i + 1  # 1-based
                    buf = raw_lines[i].rstrip("\n")
                    start = lineno
                    while buf.endswith("\\"):
                        buf = buf[:-1]  # strip trailing backslash
                        i += 1
                        if i < len(raw_lines):
                            buf += " " + raw_lines[i].rstrip("\n").strip()
                        else:
                            break
                    logical_lines.append((buf, start))
                    i += 1

                for logical_text, start_lineno in logical_lines:
                    skip, pin_error, hash_warn, pkg_name = _check_line(logical_text)
                    if skip:
                        continue
                    if pin_error:
                        findings.append(Finding(
                            checker=self.name,
                            path=rel_path,
                            line=start_lineno,
                            message="'{}' is not pinned with ==; use package==version".format(pkg_name),
                        ))
                    elif hash_warn:
                        findings.append(Finding(
                            checker=self.name,
                            path=rel_path,
                            line=start_lineno,
                            message="'{}' is pinned with == but missing --hash=; add integrity hash".format(pkg_name),
                            integrity=True,
                        ))

        return findings
