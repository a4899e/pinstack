"""Requirements checker: enforces == pinning and --hash= on requirements*.txt files.

Every requirement must be pinned with ``==`` and carry at least one valid
``--hash``. pip accepts only ``sha256``, ``sha384`` and ``sha512`` for
``--hash`` (it rejects md5, sha1 and sha224 in hash-checking mode), and each
digest must be the full hex string for its algorithm; anything else is
reported as an invalid hash.

Files pulled in with ``-r``/``--requirement`` (more requirements) or
``-c``/``--constraint`` (version constraints) are checked with the same rules,
resolved relative to the including file, even when their names do not match
``requirements*.txt`` (``-r reqs/base.txt``, Django-style
``requirements/base.txt``). An include that cannot be checked is reported:
one that is missing, outside the scan root, a URL, or written with an
environment variable. Each file is checked once, and include cycles are
followed only once.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shlex
from dataclasses import dataclass
from typing import Optional

from pinstack.core import (
    _HASH_ALGORITHMS,
    Checker,
    Finding,
    FileIndex,
    validate_url_fragment_hashes,
    is_commit_sha,
)

# Matches a package specifier line.
# Group 1: package name (including optional [extras])
# Group 2: operator (==, >=, ~=, >, <, !=, <=, ~) -- absent means bare name
# Group 3: version string -- absent means bare name
_SPECIFIER_RE = re.compile(
    r"^([A-Za-z0-9_.\-]+(?:\[[A-Za-z0-9_.,\s]+\])?)"  # name + optional extras
    r"\s*"
    r"(==|!=|~=|>=|<=|>|<|~)?"  # optional operator
    r"\s*"
    r"([^\s;#\\]*)"  # optional version
    r"\s*"
    r"(.*)"  # remainder (hashes, options, etc.)
)

# Algorithms pip accepts for --hash (pip._internal.utils.hashes.STRONG_HASHES).
_PIP_HASH_ALGORITHMS = ("sha256", "sha384", "sha512")
_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")

# -r / --requirement and -c / --constraint, in every spelling pip accepts:
# "-r file", "-rfile", "--requirement file", "--requirement=file".
_INCLUDE_RE = re.compile(
    r"^(?:-(?P<short>[rc])\s*|--(?P<long>requirement|constraint)(?:\s*=\s*|\s+))(?P<target>\S.*)$"
)


@dataclass
class _LineResult:
    """What one logical requirements line means."""

    kind: str  # skip | ok | pin_error | hash_missing | hash_invalid | include
    package: str = ""
    detail: str = ""  # the hash problem, or the include target
    include_kind: str = ""  # "-r" or "-c"


def _hash_values(line: str) -> list[str]:
    """Return the ``--hash`` values on a line (``--hash=v`` or ``--hash v``)."""
    try:
        tokens = shlex.split(line, comments=True)
    except ValueError:
        tokens = line.split()
    values: list[str] = []
    for i, token in enumerate(tokens):
        if token.startswith("--hash="):
            values.append(token[len("--hash=") :])
        elif token == "--hash" and i + 1 < len(tokens):
            values.append(tokens[i + 1])
    return values


def _hash_problem(value: str) -> Optional[str]:
    """Return why one ``--hash`` value is invalid, or None."""
    algo, sep, digest = value.partition(":")
    algo = algo.lower()
    if not sep:
        return "'{}' is not <algorithm>:<hex digest>".format(value)
    if algo not in _PIP_HASH_ALGORITHMS:
        return "'{}' uses {}, which pip does not accept for --hash".format(value, algo)
    if not digest or not _HEX_RE.match(digest):
        return "'{}' is not a hex digest".format(value)
    expected = _HASH_ALGORITHMS[algo]
    if len(digest) != expected:
        return "'{}' has {} hex characters; {} needs {}".format(
            value, len(digest), algo, expected
        )
    return None


def _hash_result(line: str, pkg_name: str) -> _LineResult:
    values = _hash_values(line)
    if not values:
        return _LineResult("hash_missing", pkg_name)
    problems = [p for p in (_hash_problem(v) for v in values) if p]
    if problems:
        return _LineResult("hash_invalid", pkg_name, detail=problems[0])
    return _LineResult("ok", pkg_name)


def _analyze_line(raw_line: str) -> _LineResult:
    """Classify one logical requirements line."""
    line = raw_line.strip()
    if line.endswith("\\"):
        line = line[:-1].strip()

    if not line or line.startswith("#"):
        return _LineResult("skip")

    include = _INCLUDE_RE.match(line)
    if include:
        flag = include.group("short") or include.group("long")[0]
        target = include.group("target").split(" #", 1)[0].strip().strip("\"'")
        return _LineResult("include", detail=target, include_kind="-" + flag)

    # Other pip options (--index-url, -f, -i, -Z, ...) install nothing.
    # Editable installs (-e) are real dependencies that bypass pinning.
    if line.startswith("--") or (line.startswith("-") and not line.startswith("-e")):
        return _LineResult("skip")
    if line.startswith("-e"):
        return _LineResult("pin_error", line[2:].strip())

    # PEP 508 direct references: "package @ URL". Only an immutable commit
    # SHA, a valid --hash, or a valid URL-fragment hash pins one.
    if " @ " in line:
        spec = line.split("--hash")[0].strip()
        url_part = spec.split(" @ ", 1)[1].strip()
        pkg_name = spec.split(" @ ", 1)[0].strip()
        if "git+" in url_part or "git://" in url_part:
            at_pos = url_part.rfind("@")
            if at_pos >= 0 and is_commit_sha(url_part[at_pos + 1 :]):
                # VCS deps are built from source, so pip cannot produce
                # --hash for them; the commit SHA is the integrity pin.
                return _LineResult("skip")
            return _LineResult("pin_error", pkg_name)
        if "--hash" in line:
            result = _hash_result(line, pkg_name)
            return _LineResult("skip") if result.kind == "ok" else result
        if not validate_url_fragment_hashes(url_part):
            return _LineResult("skip")
        return _LineResult("pin_error", pkg_name)

    if line.startswith(("http://", "https://", "git+")):
        return _LineResult("pin_error", line)

    specifier_token = line.split()[0]
    m = _SPECIFIER_RE.match(specifier_token)
    if not m:
        return _LineResult("skip")

    pkg_name = m.group(1)
    operator = m.group(2) or ""
    version = m.group(3) or ""
    # ==2.* is a prefix match, not a pin.
    if operator == "==" and version and "*" not in version:
        return _hash_result(line, pkg_name)
    return _LineResult("pin_error", pkg_name)


def _check_line(raw_line: str) -> tuple:
    """Parse a single requirements line (legacy interface).

    Returns (skip, has_pin_error, has_hash_warning, package_name). An
    include line counts as skipped here; the checker follows includes.
    """
    result = _analyze_line(raw_line)
    if result.kind in ("skip", "include", "ok"):
        return result.kind != "ok", False, False, result.package
    if result.kind == "pin_error":
        return False, True, False, result.package
    return False, False, True, result.package


def _logical_lines(raw_lines: list[str]) -> list[tuple[str, int]]:
    """Join backslash-continued lines; return (text, first line number)."""
    logical: list[tuple[str, int]] = []
    i = 0
    while i < len(raw_lines):
        start = i + 1
        buf = raw_lines[i].rstrip("\n")
        while buf.endswith("\\"):
            buf = buf[:-1]
            i += 1
            if i < len(raw_lines):
                buf += " " + raw_lines[i].rstrip("\n").strip()
            else:
                break
        logical.append((buf, start))
        i += 1
    return logical


class RequirementsChecker(Checker):
    name = "requirements"
    description = (
        "Checks requirements*.txt files, and the files they include with -r/-c,"
        " for == pinning and valid --hash= integrity markers"
    )
    patterns: list[str] = ["requirements*.txt"]

    def check(self, index: FileIndex, root: str) -> list[Finding]:
        findings: list[Finding] = []
        root_real = os.path.realpath(root)
        checked: set[str] = set()
        # Indexed files are checked in order; included files are queued
        # behind the file that includes them.
        queue: list[str] = []
        for dir_path in sorted(index.keys()):
            for fname in sorted(index[dir_path]):
                if any(fnmatch.fnmatch(fname, p) for p in self.patterns):
                    queue.append(os.path.join(dir_path, fname))

        while queue:
            full_path = queue.pop(0)
            real = os.path.realpath(full_path)
            if real in checked:
                continue
            checked.add(real)
            rel_path = os.path.relpath(full_path, root)
            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                    raw_lines = fh.readlines()
            except OSError:
                continue
            for text, lineno in _logical_lines(raw_lines):
                result = _analyze_line(text)
                if result.kind == "include":
                    target, problem = self._resolve_include(
                        full_path, result.detail, root_real
                    )
                    if problem:
                        findings.append(
                            Finding(
                                checker=self.name,
                                path=rel_path,
                                line=lineno,
                                message="{} '{}' {}".format(
                                    result.include_kind, result.detail, problem
                                ),
                                integrity=True,
                            )
                        )
                    elif target is not None:
                        queue.append(target)
                    continue
                message = self._message(result)
                if message:
                    findings.append(
                        Finding(
                            checker=self.name,
                            path=rel_path,
                            line=lineno,
                            message=message,
                            integrity=result.kind != "pin_error",
                        )
                    )

        findings.sort(key=lambda f: (f.path, f.line))
        return findings

    @staticmethod
    def _message(result: _LineResult) -> Optional[str]:
        if result.kind == "pin_error":
            return "'{}' is not pinned with ==; use package==version".format(
                result.package
            )
        if result.kind == "hash_missing":
            return (
                "'{}' is pinned with == but missing --hash=; add integrity hash".format(
                    result.package
                )
            )
        if result.kind == "hash_invalid":
            return (
                "'{}' has an invalid --hash: {}; use sha256, sha384 or sha512"
                " with the full hex digest".format(result.package, result.detail)
            )
        return None

    @staticmethod
    def _resolve_include(
        including: str, target: str, root_real: str
    ) -> tuple[Optional[str], Optional[str]]:
        """Return (path to check, None) or (None, why it cannot be checked)."""
        if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", target):
            return None, "is fetched from a URL, so its contents cannot be checked"
        if "$" in target:
            return None, "uses an environment variable, so the file cannot be checked"
        path = os.path.join(os.path.dirname(including), target)
        real = os.path.realpath(path)
        if real != root_real and not real.startswith(root_real.rstrip(os.sep) + os.sep):
            return None, "is outside the scanned directory, so it cannot be checked"
        if not os.path.isfile(real):
            return None, "does not exist, so its requirements cannot be checked"
        return path, None
