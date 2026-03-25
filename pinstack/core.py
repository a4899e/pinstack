"""Core types: Finding, Checker base, CheckerRegistry, FileIndex, runner, formatter."""

import fnmatch
import os
import sys
from dataclasses import dataclass
from typing import Dict, Set

EXCLUDED_DIRS = frozenset([
    ".git", "node_modules", ".venv", "venv", "__pycache__",
    "cdk.out", ".terraform", "target", "vendor",
])

DEFAULT_MAX_DEPTH = 4
DEFAULT_MAX_INDEX_SIZE = 384

# FileIndex: dir_path -> set of filenames
FileIndex = Dict[str, Set[str]]


@dataclass
class Finding:
    checker: str
    path: str       # relative path
    line: int       # 1-based, 0 if N/A
    message: str


class Checker:
    name = ""         # type: str
    description = ""  # type: str
    patterns = []     # type: List[str]  -- filenames or globs e.g. ["Dockerfile*", "*.dockerfile"]

    def check(self, index, root):
        # type: (FileIndex, str) -> List[Finding]
        raise NotImplementedError


class CheckerRegistry:
    def __init__(self):
        self._checkers = {}  # type: Dict[str, Type[Checker]]

    def register(self, cls):
        # type: (Type[Checker]) -> None
        self._checkers[cls.name] = cls

    def get_all(self, exclude=None):
        # type: (Optional[List[str]]) -> List[Checker]
        exclude_set = set(exclude) if exclude else set()
        return [cls() for name, cls in sorted(self._checkers.items()) if name not in exclude_set]

    def get_by_names(self, names):
        # type: (List[str]) -> List[Checker]
        result = []
        for name in names:
            if name not in self._checkers:
                raise ValueError("Unknown checker: {}".format(name))
            result.append(self._checkers[name]())
        return result

    def all_names(self):
        # type: () -> List[str]
        return sorted(self._checkers.keys())

    def get_all_patterns(self, checkers=None):
        # type: (Optional[List[Checker]]) -> Set[str]
        """Union of all patterns from given checkers (or all registered)."""
        if checkers is None:
            checkers = self.get_all()
        patterns = set()  # type: Set[str]
        for checker in checkers:
            patterns.update(checker.patterns)
        return patterns


def _matches_any_pattern(filename, patterns):
    # type: (str, Set[str]) -> bool
    """Check if filename matches any of the patterns (exact or fnmatch glob)."""
    for pattern in patterns:
        if fnmatch.fnmatch(filename, pattern):
            return True
    return False


def build_index(root, patterns, max_depth=DEFAULT_MAX_DEPTH, max_index_size=DEFAULT_MAX_INDEX_SIZE, extra_exclude_dirs=None):
    # type: (str, Set[str], int, int, Optional[Set[str]]) -> FileIndex
    """Single os.walk, filtered to only interesting files."""
    excluded = EXCLUDED_DIRS | extra_exclude_dirs if extra_exclude_dirs else EXCLUDED_DIRS
    index = {}  # type: FileIndex
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
        matched = set()  # type: Set[str]
        for fname in sorted(filenames):
            if _matches_any_pattern(fname, patterns):
                matched.add(fname)
                count += 1
                if count >= max_index_size:
                    sys.stderr.write(
                        "pinstack: index limit reached ({} files), scan may be incomplete\n".format(
                            max_index_size
                        )
                    )
                    break
        if matched:
            index[dirpath] = matched
        if count >= max_index_size:
            break
    return index


def run_checkers(checkers, index, root):
    # type: (List[Checker], FileIndex, str) -> List[Finding]
    """Run checkers against the index, return findings."""
    findings = []  # type: List[Finding]
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


def format_text(findings):
    # type: (List[Finding]) -> str
    """Format findings as plain text. No colors."""
    lines = []  # type: List[str]
    files = set()  # type: Set[str]

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
