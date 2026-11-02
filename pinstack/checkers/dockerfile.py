"""Dockerfile checker: enforces digest pinning on every image a Dockerfile pulls.

Images are pulled by three instructions:

- ``FROM <image> [AS <name>]``
- ``COPY --from=<image>`` (and ``ADD --from=``)
- ``RUN --mount=...,from=<image>``

In each position the reference may instead name a build stage declared by an
earlier ``FROM ... AS <name>`` (or, for ``--from``, a stage's numeric index).
Only those are skipped. A bare word that is not a declared stage, such as
``alpine``, is an image pulled from Docker Hub at ``:latest``.

A reference containing ``$`` is expanded from ``ARG`` values that
``--build-arg`` can override at build time, so the Dockerfile alone cannot show
which image is pulled. Those references are reported rather than trusted.

Instructions are read as logical lines: a trailing escape character (``\\`` by
default, or the character set by a ``# escape=`` parser directive) continues
the instruction onto the next line, and comment lines inside a continuation
are dropped, matching the Dockerfile reference.
"""

from __future__ import annotations

import fnmatch
import os
import re
from typing import Optional

from pinstack.core import Checker, Finding, FileIndex

# Parser directives must appear before any other content; only `escape` affects
# how instructions are split into logical lines.
_ESCAPE_DIRECTIVE_RE = re.compile(r"^#\s*escape\s*=\s*([\\`])\s*$", re.IGNORECASE)
_DIRECTIVE_RE = re.compile(r"^#\s*[a-zA-Z][a-zA-Z0-9_-]*\s*=")

# FROM [--flag=value ...] <image> [AS <name>]
_FROM_RE = re.compile(
    r"^FROM\s+(?:--\S+\s+)*(\S+)(?:\s+AS\s+(\S+))?\s*$",
    re.IGNORECASE,
)
# Instruction keyword followed by its leading ``--flag[=value]`` tokens. Flags
# precede the instruction's arguments, so a ``--from=`` or ``--mount=`` inside a
# RUN shell command or a COPY source path is not mistaken for one.
_FLAGS_RE = re.compile(r"^(COPY|ADD|RUN)((?:\s+--\S+)*)", re.IGNORECASE)


def _read_logical_lines(lines: list[str]) -> list[tuple[int, str]]:
    """Join continuation lines into logical instructions.

    :param lines: Raw file lines.
    :returns: ``(start_lineno, text)`` for every non-blank, non-comment
        logical line, with continuation escapes removed.
    """
    escape = "\\"
    # 1. Read parser directives at the top of the file.
    for raw in lines:
        stripped = raw.strip()
        m = _ESCAPE_DIRECTIVE_RE.match(stripped)
        if m:
            escape = m.group(1)
            continue
        if _DIRECTIVE_RE.match(stripped):
            continue
        break

    # 2. Join lines that end with the escape character.
    logical: list[tuple[int, str]] = []
    parts: list[str] = []
    start = 0
    for lineno, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if parts and (not stripped or stripped.startswith("#")):
            # Blank and comment lines inside a continuation are dropped.
            continue
        if not parts:
            if not stripped or stripped.startswith("#"):
                continue
            start = lineno
        if stripped.endswith(escape):
            parts.append(stripped[: -len(escape)].strip())
            continue
        parts.append(stripped)
        logical.append((start, " ".join(p for p in parts if p)))
        parts = []
    if parts:
        logical.append((start, " ".join(p for p in parts if p)))
    return logical


def _mount_from(option_list: str) -> Optional[str]:
    """Return the ``from=`` value of a ``--mount`` option list, if any.

    :param option_list: The text after ``--mount=``, e.g.
        ``type=bind,from=python:3.11,target=/src``.
    """
    for field in option_list.split(","):
        key, sep, value = field.partition("=")
        if sep and key.strip().lower() == "from":
            return value.strip().strip("\"'")
    return None


class DockerfileChecker(Checker):
    name = "dockerfile"
    description = "Checks Dockerfile image references for digest (@sha256:) pinning"
    patterns: list[str] = [
        "Dockerfile*",
        "Containerfile*",
        "*.dockerfile",
        "*.Dockerfile",
    ]

    def _image_finding(
        self, rel_path: str, lineno: int, label: str, image: str
    ) -> Finding:
        """Build the finding for an unpinned or unverifiable image reference."""
        if "$" in image:
            message = (
                "{} '{}' uses a variable reference which cannot be verified;"
                " write the image reference with an @sha256: digest".format(
                    label, image
                )
            )
        else:
            message = "{} '{}' is not pinned with @sha256: digest".format(label, image)
        return Finding(
            checker=self.name,
            path=rel_path,
            line=lineno,
            message=message,
            integrity=True,
        )

    def _check_reference(
        self,
        rel_path: str,
        lineno: int,
        label: str,
        image: str,
        stages: set[str],
        allow_index: bool,
    ) -> Optional[Finding]:
        """Check one image reference; return a finding or None when acceptable.

        :param label: Instruction text used in the message, e.g. ``FROM``.
        :param stages: Lower-cased stage names declared so far in this file.
        :param allow_index: Whether a bare integer names a stage by index
            (true for ``--from=``, not for ``FROM``).
        """
        lowered = image.lower()
        if lowered == "scratch" or lowered in stages:
            return None
        if allow_index and image.isdigit():
            return None
        if "$" in image or "@sha256:" not in image:
            return self._image_finding(rel_path, lineno, label, image)
        return None

    def _check_file(self, rel_path: str, lines: list[str]) -> list[Finding]:
        findings: list[Finding] = []
        stages: set[str] = set()

        for lineno, text in _read_logical_lines(lines):
            m = _FROM_RE.match(text)
            if m:
                image, alias = m.group(1), m.group(2)
                finding = self._check_reference(
                    rel_path, lineno, "FROM", image, stages, allow_index=False
                )
                if finding:
                    findings.append(finding)
                # The alias is visible only to instructions after this FROM.
                if alias:
                    stages.add(alias.lower())
                continue

            m = _FLAGS_RE.match(text)
            if not m:
                continue
            keyword = m.group(1).upper()
            for flag in m.group(2).split():
                name, _, value = flag.partition("=")
                name = name.lower()
                if keyword in ("COPY", "ADD") and name == "--from":
                    ref: Optional[str] = value.strip("\"'")
                    label = "{} --from".format(keyword)
                elif keyword == "RUN" and name == "--mount":
                    ref = _mount_from(value)
                    label = "RUN --mount from"
                else:
                    continue
                if not ref:
                    continue
                finding = self._check_reference(
                    rel_path, lineno, label, ref, stages, allow_index=True
                )
                if finding:
                    findings.append(finding)

        return findings

    def check(self, index: FileIndex, root: str) -> list[Finding]:
        findings: list[Finding] = []

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

                findings.extend(self._check_file(rel_path, lines))

        return findings
