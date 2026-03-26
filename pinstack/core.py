"""Core types: Finding, Checker base, CheckerRegistry, FileIndex, runner, formatter."""

from __future__ import annotations

import fnmatch
import os
import sys
from dataclasses import dataclass
from typing import Optional

EXCLUDED_DIRS = frozenset([
    ".git", "node_modules", ".venv", "venv", "__pycache__",
    "cdk.out", ".terraform", "target", "vendor",
])

# --------------------------------------------------------------------------
# URL fragment hash validation
# --------------------------------------------------------------------------
# pip supports hash verification via URL fragments in PEP 508 direct
# references and requirements files. The fragment portion of a URL (after #)
# can contain hash key-value pairs that pip uses to verify download integrity.
#
# The fragment syntax is not formally specified in the packaging standards,
# but pip's implementation (documented in pip's changelog and secure-installs
# guide) supports the following behaviors:
#
#   1. Hash algorithms are specified as key=value pairs in the fragment:
#      https://example.com/lib.tar.gz#sha256=abcdef...
#
#   2. Multiple fragment fields can coexist, separated by &. The hash does
#      not need to be the first field:
#      https://example.com/lib.tar.gz#subdirectory=src&sha256=abcdef...
#
#   3. Multiple hash algorithms can appear in the same fragment:
#      https://example.com/lib.tar.gz#sha256=abcdef...&sha512=012345...
#      If the same algorithm appears multiple times, pip uses the first value.
#
#   4. The "subdirectory" key is also valid in fragments and should not be
#      confused with a hash. Only recognized hash algorithm names count.
#
# We validate that each hash value is a hex string of the correct length for
# its algorithm. A fragment with no valid hashes is treated as unhashed.
#
# Sources:
#   - https://pip.pypa.io/en/stable/topics/secure-installs/
#   - https://pip.pypa.io/en/stable/news/
#   - https://packaging.python.org/en/latest/specifications/direct-url-data-structure/
# --------------------------------------------------------------------------

# Expected hex digest lengths per hash algorithm
_HASH_ALGORITHMS = {
    "md5": 32,
    "sha1": 40,
    "sha224": 56,
    "sha256": 64,
    "sha384": 96,
    "sha512": 128,
}

_HEX_RE = __import__("re").compile(r'^[0-9a-fA-F]+$')


def validate_url_fragment_hashes(url: str) -> list[str]:
    """Validate hash key-value pairs in a URL fragment.

    Returns a list of error strings. An empty list means all hashes are valid.
    If the URL has no fragment or no hash fields, returns a single error
    indicating no hash verification is present.
    """
    if "#" not in url:
        return ["no hash fragment in URL"]

    fragment = url.split("#", 1)[1]
    fields = fragment.split("&")

    errors: list[str] = []
    found_any_hash = False

    for field in fields:
        if "=" not in field:
            continue
        key, _, value = field.partition("=")
        key = key.strip().lower()

        if key not in _HASH_ALGORITHMS:
            continue  # skip non-hash fields like subdirectory=

        found_any_hash = True
        expected_len = _HASH_ALGORITHMS[key]

        if not value:
            errors.append("{} hash is empty".format(key))
        elif not _HEX_RE.match(value):
            errors.append("{} hash contains non-hex characters: {}".format(key, value))
        elif len(value) != expected_len:
            errors.append("{} hash has wrong length: expected {} hex chars, got {}".format(
                key, expected_len, len(value),
            ))

    if not found_any_hash:
        return ["no hash algorithm found in URL fragment"]

    return errors


DEFAULT_MAX_DEPTH = 4
DEFAULT_MAX_INDEX_SIZE = 384

# FileIndex: dir_path -> set of filenames
FileIndex = dict[str, set[str]]


@dataclass
class Finding:
    checker: str
    path: str       # relative path
    line: int       # 1-based, 0 if N/A
    message: str
    integrity: bool = False  # True when finding is about missing hash/checksum/integrity


class Checker:
    name: str = ""
    description: str = ""
    patterns: list[str] = []     # filenames or globs e.g. ["Dockerfile*", "*.dockerfile"]

    def check(self, index: FileIndex, root: str) -> list[Finding]:
        raise NotImplementedError


class CheckerRegistry:
    def __init__(self) -> None:
        self._checkers: dict[str, type[Checker]] = {}

    def register(self, cls: type[Checker]) -> None:
        self._checkers[cls.name] = cls

    def get_all(self, exclude: Optional[list[str]] = None) -> list[Checker]:
        exclude_set = set(exclude) if exclude else set()
        return [cls() for name, cls in sorted(self._checkers.items()) if name not in exclude_set]

    def get_by_names(self, names: list[str]) -> list[Checker]:
        result = []
        for name in names:
            if name not in self._checkers:
                raise ValueError("Unknown checker: {}".format(name))
            result.append(self._checkers[name]())
        return result

    def all_names(self) -> list[str]:
        return sorted(self._checkers.keys())

    def get_all_patterns(self, checkers: Optional[list[Checker]] = None) -> set[str]:
        """Union of all patterns from given checkers (or all registered)."""
        if checkers is None:
            checkers = self.get_all()
        patterns: set[str] = set()
        for checker in checkers:
            patterns.update(checker.patterns)
        return patterns


def _matches_any_pattern(filename: str, patterns: set[str]) -> bool:
    """Check if filename matches any of the patterns (exact or fnmatch glob)."""
    for pattern in patterns:
        if fnmatch.fnmatch(filename, pattern):
            return True
    return False


def build_index(root: str, patterns: set[str], max_depth: int = DEFAULT_MAX_DEPTH, max_index_size: int = DEFAULT_MAX_INDEX_SIZE, extra_exclude_dirs: Optional[set[str]] = None) -> FileIndex:
    """Single os.walk, filtered to only interesting files."""
    excluded = EXCLUDED_DIRS | extra_exclude_dirs if extra_exclude_dirs else EXCLUDED_DIRS
    index: FileIndex = {}
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune excluded dirs (modify in-place)
        dirnames[:] = sorted(d for d in dirnames if d not in excluded)
        # Enforce max depth
        rel = os.path.relpath(dirpath, root)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if depth >= max_depth:
            dirnames.clear()
            continue
        # Filter to interesting files
        matched: set[str] = set()
        for fname in sorted(filenames):
            if _matches_any_pattern(fname, patterns):
                matched.add(fname)
                count += 1
                if count >= max_index_size:
                    sys.stderr.write(
                        "pinstack: index limit reached ({} files), scan may be incomplete. "
                        "Use --max-files to increase the limit.\n".format(
                            max_index_size
                        )
                    )
                    break
        if matched:
            index[dirpath] = matched
        if count >= max_index_size:
            break
    return index


def run_checkers(checkers: list[Checker], index: FileIndex, root: str) -> list[Finding]:
    """Run checkers against the index, return findings."""
    findings: list[Finding] = []
    for checker in checkers:
        try:
            results = checker.check(index, root)
        except Exception as exc:
            results = [Finding(
                checker=checker.name,
                path="<internal>",
                line=0,
                message="Checker crashed: {}".format(exc),
            )]
        findings.extend(results)

    # Sort by path then line
    findings.sort(key=lambda f: (f.path, f.line))
    return findings


def format_text(findings: list[Finding]) -> str:
    """Format findings as plain text. No colors."""
    lines: list[str] = []
    files: set[str] = set()

    for f in findings:
        files.add(f.path)
        if f.line > 0:
            location = "{}:{}".format(f.path, f.line)
        else:
            location = f.path
        lines.append("FAIL  {}  {}".format(location, f.message))

    # Summary line
    count = len(findings)
    file_count = len(files)
    if count == 0:
        summary = "0 findings"
    else:
        summary = "{} error{} in {} file{}".format(
            count, "s" if count != 1 else "",
            file_count, "s" if file_count != 1 else "",
        )
    lines.append("")
    lines.append(summary)
    return "\n".join(lines)
