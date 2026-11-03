"""Helm checker: exact dependency versions, a Chart.lock, and its drift digest.

For a chart that declares ``dependencies:`` in Chart.yaml:

- every dependency ``version`` must be one exact semantic version
  (``1.2.3``, optionally with a pre-release or build suffix). Helm reads the
  field as a Masterminds semver constraint, so ``17.x.x``, ``^1.2.0``,
  ``~1.2``, ``>=1.0`` and a partial ``1.2`` all select a range. A dependency
  whose chart comes from this repository is exempt: a ``file://``
  repository, or no repository at all (a subchart vendored in ``charts/``);
- a Chart.lock must exist, list every dependency, record exact versions, and
  agree with the exact versions Chart.yaml asks for;
- the Chart.lock must carry its ``digest:`` line.

What the Chart.lock ``digest:`` is: Helm computes it from the dependency
list in Chart.yaml and the lock itself, so that ``helm dependency build`` can
tell the lock is out of date. It is drift detection, not a hash of any chart
archive: Helm 3 does not record or verify chart content in Chart.lock. So
the findings here are about version pinning and lock freshness, and are not
marked as integrity findings.

Dependencies must be a block list (``- name: x`` items). A flow-style list
or item cannot be read here and is reported rather than passed.

Sources:
  - https://helm.sh/docs/helm/helm_dependency/
  - https://github.com/Masterminds/semver#checking-version-constraints
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional

from pinstack.core import Checker, Finding, FileIndex, describe_missing

# One exact version as Masterminds semver reads it (Helm's constraint
# library), with the optional "=" or "v" it tolerates.
_EXACT_SEMVER_RE = re.compile(
    r"^=?v?(\d+\.\d+\.\d+"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?)$"
)
_KEY_VALUE_RE = re.compile(
    r"""^(?:"([^"]+)"|'([^']+)'|([A-Za-z_][\w-]*))\s*:(?:\s+(.*))?$"""
)
_EMPTY_VALUES = ("", "[]", "null", "~")


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _strip_comment(line: str) -> str:
    """Remove a ``#`` comment that starts the line or follows whitespace."""
    quote = ""
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in ("'", '"') and (i == 0 or line[i - 1] in " \t:-"):
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i].rstrip()
    return line.rstrip()


def _dash_item(text: str) -> Optional[str]:
    """Return the content after a sequence dash (``- x``, ``-\\tx``, ``-``), or None."""
    if text == "-":
        return ""
    if text[:1] == "-" and text[1:2] in (" ", "\t"):
        return text[2:].strip()
    return None


@dataclass
class _ChartFile:
    """What a Chart.yaml or Chart.lock says about dependencies."""

    dependencies: list[tuple[int, dict[str, str]]] = field(default_factory=list)
    flow_line: int = 0  # a flow-style list or item that could not be read
    has_digest: bool = False


def _parse(lines: list[str]) -> _ChartFile:
    """Read the top-level ``dependencies:`` list and ``digest:`` in one pass.

    A new entry opens only at the list's own dash column (set by its first
    item); only keys at the entry's key column are read, so a nested list
    such as ``tags:`` or ``import-values:`` stays inside its entry.
    """
    chart = _ChartFile()
    in_section = False
    dash_col: Optional[int] = None
    key_col: Optional[int] = None
    current: Optional[dict[str, str]] = None
    for lineno, raw in enumerate(lines, start=1):
        line = _strip_comment(raw)
        text = line.strip()
        if not text:
            continue
        indent = len(line) - len(line.lstrip())
        item = _dash_item(text)

        if indent == 0 and item is None:
            m = _KEY_VALUE_RE.match(text)
            key = next((g for g in m.groups()[:3] if g), "") if m else ""
            value = (m.group(4) or "").strip() if m else ""
            in_section = key == "dependencies"
            current = None
            if in_section and value not in _EMPTY_VALUES:
                chart.flow_line = lineno
            if key == "digest" and value:
                chart.has_digest = True
            continue
        if not in_section:
            continue

        if item is not None and (dash_col is None or indent == dash_col):
            dash_col = indent
            current = {}
            chart.dependencies.append((lineno, current))
            if item.startswith(("{", "[")):
                chart.flow_line = chart.flow_line or lineno
                current = None
                continue
            # The key column: where the item's content starts after the dash.
            key_col = (
                indent + 1 + (len(text) - 1 - len(text[1:].lstrip())) if item else None
            )
            text = item
            if not text:
                continue
        elif current is None or dash_col is None or indent <= dash_col:
            continue
        elif key_col is None:
            key_col = indent  # first key of an item whose dash stood alone
        elif indent != key_col:
            continue  # nested content of the current entry

        m = _KEY_VALUE_RE.match(text)
        if m and current is not None:
            key = next(g for g in m.groups()[:3] if g)
            current.setdefault(key, _unquote(m.group(4) or ""))
    return chart


def _read(path: str) -> Optional[list[str]]:
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            return fh.read().splitlines()
    except OSError:
        return None


def _exact(version: str) -> Optional[str]:
    """Return the version without ``=``/``v`` if it is one exact version."""
    m = _EXACT_SEMVER_RE.match(version)
    return m.group(1) if m else None


class HelmChecker(Checker):
    name = "helm"
    description = (
        "Checks Chart.yaml dependencies for exact versions, and that Chart.lock"
        " exists, matches them, and carries its digest"
    )
    patterns: list[str] = ["Chart.yaml", "Chart.lock"]

    def check(self, index: FileIndex, root: str) -> list[Finding]:
        findings: list[Finding] = []
        for dir_path in sorted(index.keys()):
            present = index[dir_path]
            if "Chart.yaml" in present or "Chart.lock" in present:
                findings.extend(self._check_dir(dir_path, present, root))
        return findings

    def _check_dir(self, dir_path: str, present: set[str], root: str) -> list[Finding]:
        findings: list[Finding] = []

        def add(fname: str, line: int, message: str) -> None:
            findings.append(
                Finding(
                    checker=self.name,
                    path=os.path.relpath(os.path.join(dir_path, fname), root),
                    line=line,
                    message=message,
                )
            )

        chart = _ChartFile()
        if "Chart.yaml" in present:
            yaml_lines = _read(os.path.join(dir_path, "Chart.yaml"))
            if yaml_lines is not None:
                chart = _parse(yaml_lines)
        if chart.flow_line:
            add(
                "Chart.yaml",
                chart.flow_line,
                "dependencies are written in flow style, which pinstack cannot"
                " read; write them as a block list",
            )

        # 1. Chart.yaml versions must be exact, except for local charts.
        wanted: dict[str, str] = {}  # name -> exact version Chart.yaml asks for
        for lineno, dep in chart.dependencies:
            if not dep:
                continue  # a flow-style item, reported above
            name = dep.get("name", "unknown")
            version = dep.get("version", "")
            repository = dep.get("repository", "")
            if not repository or repository.startswith("file://"):
                continue  # a chart in this repository; nothing is fetched
            exact = _exact(version)
            if exact:
                wanted[name] = exact
            elif not version:
                add(
                    "Chart.yaml",
                    lineno,
                    "dependency '{}' has no version; pin to an exact version".format(
                        name
                    ),
                )
            else:
                add(
                    "Chart.yaml",
                    lineno,
                    "dependency '{}' uses version range '{}'; pin to an exact"
                    " version".format(name, version),
                )

        has_deps = bool(chart.dependencies) or bool(chart.flow_line)
        if "Chart.lock" not in present:
            if has_deps:
                add(
                    "Chart.yaml",
                    0,
                    "Chart.yaml declares dependencies but Chart.lock is missing;"
                    " run 'helm dependency update'",
                )
            return findings
        lock_lines = _read(os.path.join(dir_path, "Chart.lock"))
        if lock_lines is None:
            add("Chart.lock", 0, "Chart.lock could not be read")
            return findings
        lock = _parse(lock_lines)

        # 2. The lock records exact versions and its drift digest.
        locked: dict[str, str] = {}
        for lineno, dep in lock.dependencies:
            name = dep.get("name", "unknown")
            version = dep.get("version", "")
            exact = _exact(version)
            locked[name] = exact or version
            if not exact:
                add(
                    "Chart.lock",
                    lineno,
                    "dependency '{}' in Chart.lock has version '{}', which is not"
                    " exact; run 'helm dependency update'".format(name, version),
                )
        if lock.dependencies and not lock.has_digest:
            add(
                "Chart.lock",
                0,
                "Chart.lock has no digest: line, so Helm cannot tell when it is"
                " out of date with Chart.yaml; run 'helm dependency update'",
            )

        # 3. Every Chart.yaml dependency is in the lock, at the version asked for.
        declared = {dep.get("name", "") for _, dep in chart.dependencies} - {""}
        missing = sorted(declared - set(locked))
        if missing:
            add(
                "Chart.lock",
                0,
                "Chart.lock is stale: {}".format(describe_missing(missing)),
            )
        for name, version in sorted(wanted.items()):
            if name in locked and locked[name] != version:
                add(
                    "Chart.lock",
                    0,
                    "Chart.lock is stale: '{}' is locked at {} but Chart.yaml asks"
                    " for {}; run 'helm dependency update'".format(
                        name, locked[name], version
                    ),
                )
        return findings
