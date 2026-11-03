"""Helm checker: exact dependency versions, a Chart.lock, and its drift digest.

For a chart that declares ``dependencies:`` in Chart.yaml:

- every dependency ``version`` must be one exact semantic version
  (``1.2.3``, optionally with a pre-release or build suffix). Helm reads the
  field as a Masterminds semver constraint, so ``17.x.x``, ``^1.2.0``,
  ``~1.2``, ``>=1.0`` and a partial ``1.2`` all select a range. A
  ``file://`` dependency is a chart in the same repository and is exempt;
- a Chart.lock must exist, list every dependency, and record exact versions;
- the Chart.lock must carry its ``digest:`` line.

What the Chart.lock ``digest:`` is: Helm computes it from the dependency
list in Chart.yaml and the lock itself, so that ``helm dependency build`` can
tell the lock is out of date. It is drift detection, not a hash of any chart
archive: Helm 3 does not record or verify chart content in Chart.lock. So
the findings here are about version pinning and lock freshness, and are not
marked as integrity findings.

Sources:
  - https://helm.sh/docs/helm/helm_dependency/
  - https://github.com/Masterminds/semver#checking-version-constraints
"""

from __future__ import annotations

import os
import re
from typing import Optional

from pinstack.core import Checker, Finding, FileIndex

_EXACT_SEMVER_RE = re.compile(
    r"^=?v?\d+\.\d+\.\d+"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_KEY_VALUE_RE = re.compile(r"^([A-Za-z_][\w-]*)\s*:(?:\s+(.*))?$")


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


def _read_lines(path: str) -> Optional[list[str]]:
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            return fh.read().splitlines()
    except OSError:
        return None


def _dependencies(lines: list[str]) -> list[tuple[int, dict[str, str]]]:
    """Return ``(line, fields)`` for each entry of the top-level ``dependencies:``.

    Entries are block-sequence items (``- name: x`` then more keys at the
    item's indentation), in any key order, with quoted or plain values.
    """
    entries: list[tuple[int, dict[str, str]]] = []
    in_section = False
    item_col: Optional[int] = None
    current: Optional[dict[str, str]] = None
    for lineno, raw in enumerate(lines, start=1):
        line = _strip_comment(raw)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        text = line.strip()
        if indent == 0 and not text.startswith("-"):
            in_section = text.startswith("dependencies:")
            current = None
            continue
        if not in_section:
            continue
        if text.startswith("- ") or text == "-":
            current = {}
            entries.append((lineno, current))
            item_col = indent + 2
            text = text[2:].strip()
            if not text:
                continue
        elif current is None or item_col is None or indent < item_col:
            continue
        m = _KEY_VALUE_RE.match(text)
        if m and current is not None:
            current.setdefault(m.group(1), _unquote(m.group(2) or ""))
    return entries


def _inline_dependencies(lines: list[str]) -> int:
    """Return the line of a top-level ``dependencies:`` given an inline value
    other than an empty list or null (flow style), else 0."""
    for lineno, raw in enumerate(lines, start=1):
        line = _strip_comment(raw)
        if line.startswith("dependencies:"):
            value = line[len("dependencies:") :].strip()
            if value and value not in ("[]", "null", "~"):
                return lineno
    return 0


class HelmChecker(Checker):
    name = "helm"
    description = (
        "Checks Chart.yaml dependencies for exact versions, and that Chart.lock"
        " exists, covers them, and carries its digest"
    )
    patterns: list[str] = ["Chart.yaml", "Chart.lock"]

    def check(self, index: FileIndex, root: str) -> list[Finding]:
        findings: list[Finding] = []

        for dir_path in sorted(index.keys()):
            present = index[dir_path]
            if "Chart.yaml" not in present and "Chart.lock" not in present:
                continue

            def finding(fname: str, line: int, message: str) -> Finding:
                return Finding(
                    checker=self.name,
                    path=os.path.relpath(os.path.join(dir_path, fname), root),
                    line=line,
                    message=message,
                )

            yaml_lines = (
                _read_lines(os.path.join(dir_path, "Chart.yaml"))
                if "Chart.yaml" in present
                else None
            )
            lock_lines = (
                _read_lines(os.path.join(dir_path, "Chart.lock"))
                if "Chart.lock" in present
                else None
            )
            yaml_deps = _dependencies(yaml_lines) if yaml_lines is not None else []
            lock_deps = _dependencies(lock_lines) if lock_lines is not None else []
            inline = _inline_dependencies(yaml_lines or [])
            if inline:
                findings.append(
                    finding(
                        "Chart.yaml",
                        inline,
                        "dependencies are written in flow style, which pinstack"
                        " cannot read; write them as a block list",
                    )
                )

            # 1. Chart.yaml versions must be exact.
            for lineno, dep in yaml_deps:
                name = dep.get("name", "unknown")
                version = dep.get("version", "")
                if dep.get("repository", "").startswith("file://"):
                    continue  # a chart in this repository; nothing is fetched
                if not version:
                    findings.append(
                        finding(
                            "Chart.yaml",
                            lineno,
                            "dependency '{}' has no version; pin to an exact"
                            " version".format(name),
                        )
                    )
                elif not _EXACT_SEMVER_RE.match(version):
                    findings.append(
                        finding(
                            "Chart.yaml",
                            lineno,
                            "dependency '{}' uses version range '{}'; pin to an"
                            " exact version".format(name, version),
                        )
                    )

            if yaml_deps and lock_lines is None:
                findings.append(
                    finding(
                        "Chart.yaml",
                        0,
                        "Chart.yaml declares dependencies but Chart.lock is missing;"
                        " run 'helm dependency update'",
                    )
                )
                continue
            if lock_lines is None:
                continue

            # 2. The lock records exact versions and its drift digest.
            for lineno, dep in lock_deps:
                version = dep.get("version", "")
                if not _EXACT_SEMVER_RE.match(version):
                    findings.append(
                        finding(
                            "Chart.lock",
                            lineno,
                            "dependency '{}' in Chart.lock has version '{}', which"
                            " is not exact; run 'helm dependency update'".format(
                                dep.get("name", "unknown"), version
                            ),
                        )
                    )
            if lock_deps and not any(
                _strip_comment(ln).lstrip().startswith("digest:") for ln in lock_lines
            ):
                findings.append(
                    finding(
                        "Chart.lock",
                        0,
                        "Chart.lock has no digest: line, so Helm cannot tell when it"
                        " is out of date with Chart.yaml; run 'helm dependency"
                        " update'",
                    )
                )

            # 3. Every Chart.yaml dependency must be in the lock.
            locked = {dep.get("name") for _, dep in lock_deps}
            missing = sorted(
                {dep.get("name", "") for _, dep in yaml_deps} - locked - {""}
            )
            if missing:
                findings.append(
                    finding(
                        "Chart.lock",
                        0,
                        "Chart.lock is stale: missing {} ({})".format(
                            "1 dependency"
                            if len(missing) == 1
                            else "{} dependencies".format(len(missing)),
                            ", ".join(missing),
                        ),
                    )
                )

        return findings
