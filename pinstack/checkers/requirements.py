"""Requirements checker: enforces == pinning and --hash= on requirements*.txt files.

Every requirement must be pinned with ``==`` and carry at least one valid
``--hash``. pip accepts only ``sha256``, ``sha384`` and ``sha512`` (lower
case) for ``--hash``, and each digest must be the full hex string for its
algorithm; anything else is reported as an invalid hash.

Files pulled in with ``-r``/``--requirement`` are checked with the same
rules, resolved relative to the including file, even when their names do
not match ``requirements*.txt`` (``-r reqs/base.txt``, Django-style
``requirements/base.txt``). An include that cannot be checked is reported:
one that is missing, outside the scan root (also through a symlink), a URL,
or built from a ``${VAR}`` that pip expands from the environment. Each file
is checked once, so include cycles terminate.

``-c``/``--constraint`` files are not checked: constraints install nothing
and only narrow the versions of packages that are installed for another
reason, which the requirement lines (pinned and hashed) already decide.

Lines are read the way pip reads them (``pip._internal.req.req_file``):
continuation lines are joined, except that a comment line never continues;
``#`` starts a comment only at the start of a line or after whitespace; the
requirement is everything before the first token that starts with ``-``, and
the rest is parsed as options, several per line, with unambiguous prefixes
of long options accepted as optparse does.

Source: https://pip.pypa.io/en/stable/reference/requirements-file-format/
"""

from __future__ import annotations

import collections
import fnmatch
import os
import re
import shlex
from dataclasses import dataclass, field
from typing import Optional

from pinstack.core import (
    Checker,
    Finding,
    FileIndex,
    hex_digest_problem,
    validate_url_fragment_hashes,
    is_commit_sha,
)

# Matches a package specifier token.
# Group 1: package name (including optional [extras])
# Group 2: operator (==, >=, ~=, >, <, !=, <=, ~) -- absent means bare name
# Group 3: version string -- absent means bare name
_SPECIFIER_RE = re.compile(
    r"^([A-Za-z0-9_.\-]+(?:\[[A-Za-z0-9_.,\s]+\])?)"  # name + optional extras
    r"\s*"
    r"(==|!=|~=|>=|<=|>|<|~)?"  # optional operator
    r"\s*"
    r"([^\s;#\\]*)"  # optional version
)

# pip's COMMENT_RE and the ${VAR} syntax it expands from the environment.
_COMMENT_RE = re.compile(r"(^|\s+)#.*$")
_ENV_VAR_RE = re.compile(r"\$\{[A-Z0-9_]+\}")
_URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")

# Algorithms pip accepts for --hash (pip._internal.utils.hashes.STRONG_HASHES).
_PIP_HASH_ALGORITHMS = ("sha256", "sha384", "sha512")

# The options of a requirements file line. Each long name maps to whether
# it takes a value; short names map to their long name.
_LONG_OPTIONS = {
    "requirement": True,
    "constraint": True,
    "editable": True,
    "index-url": True,
    "extra-index-url": True,
    "no-index": False,
    "find-links": True,
    "pre": False,
    "prefer-binary": False,
    "require-hashes": False,
    "trusted-host": True,
    "use-feature": True,
    "only-binary": True,
    "no-binary": True,
    "hash": True,
    "global-option": True,
    "config-settings": True,
}
_SHORT_OPTIONS = {
    "r": "requirement",
    "c": "constraint",
    "e": "editable",
    "i": "index-url",
    "f": "find-links",
    "Z": None,  # --always-unzip, accepted and ignored by older pip
}


@dataclass
class _Line:
    """One logical requirements line: the requirement and its options."""

    requirement: str
    options: list[tuple[str, str]] = field(default_factory=list)  # (long name, value)

    def values(self, name: str) -> list[str]:
        return [value for opt, value in self.options if opt == name]


@dataclass
class _LineResult:
    """What one logical requirements line means for the gate."""

    kind: str  # skip | ok | pin_error | hash_missing | hash_invalid
    package: str = ""
    detail: str = ""


def _logical_lines(raw_lines: list[str]) -> list[tuple[str, int]]:
    """Join continuation lines as pip does; return (text, first line number).

    A line ending in ``\\`` continues onto the next unless it is a comment
    line; the pieces are joined with the backslash removed.
    """
    logical: list[tuple[str, int]] = []
    pending: list[str] = []
    start = 0
    for lineno, raw in enumerate(raw_lines, start=1):
        line = raw.rstrip("\r\n")
        if not line.endswith("\\") or _COMMENT_RE.match(line):
            if _COMMENT_RE.match(line):
                line = " " + line  # keep it a comment once joined
            if pending:
                pending.append(line)
                logical.append(("".join(pending), start))
                pending = []
            else:
                logical.append((line, lineno))
        else:
            if not pending:
                start = lineno
            pending.append(line.strip("\\"))
    if pending:
        logical.append(("".join(pending), start))
    return logical


def _parse_options(tokens: list[str]) -> list[tuple[str, str]]:
    """Parse option tokens the way optparse does for a requirements line.

    Parsing stops at the first token pip would reject (an unknown option or
    a missing value); pip refuses such a file, so nothing after it installs.
    """
    options: list[tuple[str, str]] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        i += 1
        if token.startswith("--"):
            name, sep, value = token[2:].partition("=")
            matches = [o for o in _LONG_OPTIONS if o == name] or [
                o for o in _LONG_OPTIONS if o.startswith(name)
            ]
            if len(matches) != 1:
                break
            name = matches[0]
            if not _LONG_OPTIONS[name]:
                options.append((name, ""))
                continue
            if not sep:
                if i >= len(tokens):
                    break
                value = tokens[i]
                i += 1
            options.append((name, value))
        elif token.startswith("-") and len(token) >= 2 and token[1] in _SHORT_OPTIONS:
            long_name = _SHORT_OPTIONS[token[1]]
            if long_name is None:
                continue
            value = token[2:]
            if not value:
                if i >= len(tokens):
                    break
                value = tokens[i]
                i += 1
            options.append((long_name, value))
        else:
            break
    return options


def _split_line(text: str) -> Optional[_Line]:
    """Split one logical line into its requirement and options, or None if empty."""
    text = _COMMENT_RE.sub("", text).strip()
    if not text:
        return None
    # pip: the requirement is every space-separated token before the first
    # one that starts with "-"; the rest are options.
    tokens = text.split(" ")
    requirement, option_text = text, ""
    for index, token in enumerate(tokens):
        if token.startswith("-"):
            requirement = " ".join(tokens[:index]).strip()
            option_text = " ".join(tokens[index:])
            break
    try:
        option_tokens = shlex.split(option_text)
    except ValueError:
        option_tokens = option_text.split()
    return _Line(requirement=requirement, options=_parse_options(option_tokens))


def _hash_problem(value: str) -> Optional[str]:
    """Return why one ``--hash`` value is invalid, or None."""
    algo, sep, digest = value.partition(":")
    if not sep:
        return "'{}' is not <algorithm>:<hex digest>".format(value)
    if algo not in _PIP_HASH_ALGORITHMS:
        return "'{}' uses {}, which pip does not accept for --hash".format(value, algo)
    problem = hex_digest_problem(algo, digest)
    return "'{}': {}".format(value, problem) if problem else None


def _hash_result(line: _Line, pkg_name: str) -> _LineResult:
    values = line.values("hash")
    if not values:
        return _LineResult("hash_missing", pkg_name)
    problems = [p for p in (_hash_problem(v) for v in values) if p]
    if problems:
        return _LineResult("hash_invalid", pkg_name, detail=problems[0])
    return _LineResult("ok", pkg_name)


def _requirement_result(line: _Line) -> _LineResult:
    """Classify the requirement part of a line (empty means options only)."""
    spec = line.requirement
    if not spec:
        return _LineResult("skip")

    # PEP 508 direct references: "package @ URL". Only an immutable commit
    # SHA, a valid --hash, or a valid URL-fragment hash pins one.
    if " @ " in spec:
        pkg_name, url_part = (part.strip() for part in spec.split(" @ ", 1))
        if "git+" in url_part or "git://" in url_part:
            at_pos = url_part.rfind("@")
            if at_pos >= 0 and is_commit_sha(url_part[at_pos + 1 :].split("#")[0]):
                # VCS deps are built from source, so pip cannot produce
                # --hash for them; the commit SHA is the integrity pin.
                return _LineResult("skip")
            return _LineResult("pin_error", pkg_name)
        if line.values("hash"):
            result = _hash_result(line, pkg_name)
            return _LineResult("skip") if result.kind == "ok" else result
        if not validate_url_fragment_hashes(url_part):
            return _LineResult("skip")
        return _LineResult("pin_error", pkg_name)

    if _URL_RE.match(spec) or spec.startswith("git+"):
        return _LineResult("pin_error", spec)

    m = _SPECIFIER_RE.match(spec.split()[0])
    if not m:
        return _LineResult("skip")
    pkg_name, operator, version = m.group(1), m.group(2) or "", m.group(3) or ""
    # ==2.* is a prefix match, not a pin.
    if operator == "==" and version and "*" not in version:
        return _hash_result(line, pkg_name)
    return _LineResult("pin_error", pkg_name)


def _analyze_line(text: str) -> list[_LineResult]:
    """Return the gate results for one logical line (requirement and editables)."""
    line = _split_line(text)
    if line is None:
        return []
    results = [_requirement_result(line)]
    # Editable installs (-e/--editable) are not content-addressed.
    results.extend(_LineResult("pin_error", value) for value in line.values("editable"))
    return [r for r in results if r.kind not in ("skip", "ok")]


class RequirementsChecker(Checker):
    name = "requirements"
    description = (
        "Checks requirements*.txt files, and the files they include with -r,"
        " for == pinning and valid --hash= integrity markers"
    )
    patterns: list[str] = ["requirements*.txt"]

    def check(self, index: FileIndex, root: str) -> list[Finding]:
        findings: list[Finding] = []
        root_real = os.path.realpath(root)
        checked: set[str] = set()
        # Indexed files first; included files are queued behind them.
        queue: collections.deque[str] = collections.deque(
            os.path.join(dir_path, fname)
            for dir_path in sorted(index.keys())
            for fname in sorted(index[dir_path])
            if any(fnmatch.fnmatch(fname, p) for p in self.patterns)
        )

        while queue:
            full_path = queue.popleft()
            real = os.path.realpath(full_path)
            if real in checked:
                continue
            checked.add(real)
            rel_path = os.path.relpath(full_path, root)
            try:
                with open(full_path, "r", encoding="utf-8-sig", errors="replace") as fh:
                    raw_lines = fh.readlines()
            except OSError:
                continue

            for text, lineno in _logical_lines(raw_lines):
                line = _split_line(text)
                if line is None:
                    continue
                for target in line.values("requirement"):
                    path, problem = self._resolve_include(full_path, target, root_real)
                    if problem:
                        findings.append(
                            Finding(
                                checker=self.name,
                                path=rel_path,
                                line=lineno,
                                message="-r '{}' {}".format(target, problem),
                                integrity=True,
                            )
                        )
                    elif path is not None:
                        queue.append(path)
                for result in _analyze_line(text):
                    findings.append(
                        Finding(
                            checker=self.name,
                            path=rel_path,
                            line=lineno,
                            message=self._message(result),
                            integrity=result.kind != "pin_error",
                        )
                    )

        findings.sort(key=lambda f: (f.path, f.line))
        return findings

    @staticmethod
    def _message(result: _LineResult) -> str:
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
        return (
            "'{}' has an invalid --hash {}; use sha256, sha384 or sha512 with the"
            " full hex digest".format(result.package, result.detail)
        )

    @staticmethod
    def _resolve_include(
        including: str, target: str, root_real: str
    ) -> tuple[Optional[str], Optional[str]]:
        """Return (path to check, None) or (None, why it cannot be checked)."""
        if _URL_RE.match(target):
            return None, "is fetched from a URL, so its contents cannot be checked"
        if _ENV_VAR_RE.search(target):
            return None, "uses an environment variable, so the file cannot be checked"
        path = os.path.join(os.path.dirname(including), target)
        real = os.path.realpath(path)
        if real != root_real and not real.startswith(root_real.rstrip(os.sep) + os.sep):
            return None, "is outside the scanned directory, so it cannot be checked"
        if not os.path.isfile(real):
            return None, "does not exist, so its requirements cannot be checked"
        return path, None
