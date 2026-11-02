"""Dockerfile checker: enforces digest pinning on every image a Dockerfile pulls.

Images are pulled from four places:

- the ``# syntax=<image>`` parser directive (the BuildKit frontend)
- ``FROM <image> [AS <name>]``
- ``COPY --from=<image>`` and ``ADD --from=<image>``
- ``RUN --mount=...,from=<image>``

``ONBUILD`` instructions are checked too, since they run in every downstream
build.

Outside the directive, the reference may instead name a build stage declared
by an earlier ``FROM ... AS <name>`` (or, for ``--from``, a stage's numeric
index). Only those are skipped. A bare word that is not a declared stage, such
as ``alpine``, is an image pulled from Docker Hub at ``:latest``.

A reference containing ``$`` is expanded from ``ARG`` values that
``--build-arg`` can override at build time, so the Dockerfile alone cannot show
which image is pulled. Those references are reported rather than trusted.

The file is split into instructions the way BuildKit's parser does it:

1. Parser directives are read only from the leading comment lines. Only
   ``syntax``, ``escape`` and ``check`` are directives; an unknown one is a
   comment and ends directive parsing, as does a blank line or an instruction.
2. A line ending in the escape character (``\\`` unless ``# escape=`` says
   otherwise, optionally followed by spaces) continues onto the next line. The
   escape is removed and the next line is appended as-is, without a separator,
   so ``FROM alp\\`` + ``ine`` is ``FROM alpine``. Comment lines and blank lines
   inside a continuation are dropped.
3. ``RUN``, ``COPY`` and ``ADD`` may open heredocs (``<<EOF``, ``<<-"EOF"``);
   the body lines up to each delimiter are content, not instructions.

Source: https://docs.docker.com/reference/dockerfile/
"""

from __future__ import annotations

import csv
import fnmatch
import os
import re
from typing import Optional

from pinstack.core import Checker, Finding, FileIndex

_KNOWN_DIRECTIVES = frozenset(["syntax", "escape", "check"])
_DIRECTIVE_RE = re.compile(r"^#\s*([a-zA-Z][a-zA-Z0-9_-]*)\s*=\s*(.*?)\s*$")

# FROM [--flag=value ...] <image> [AS <name>]
_FROM_RE = re.compile(
    r"^FROM\s+(?:--\S+\s+)*(\S+)(?:\s+AS\s+(\S+))?\s*$",
    re.IGNORECASE,
)
_ONBUILD_RE = re.compile(r"^ONBUILD\s+", re.IGNORECASE)
# Instruction keyword followed by its leading ``--flag[=value]`` tokens. Flags
# precede the instruction's arguments, so a ``--from=`` or ``--mount=`` inside a
# RUN shell command or a COPY source path is not mistaken for one.
_FLAGS_RE = re.compile(r"^(COPY|ADD|RUN)((?:\s+--\S+)*)", re.IGNORECASE)
_HEREDOC_INSTRUCTION_RE = re.compile(r"^(?:RUN|COPY|ADD)\s", re.IGNORECASE)
_HEREDOC_RE = re.compile(r"<<(-?)([\"']?)([A-Za-z_][A-Za-z0-9_]*)\2")


def _read_directives(lines: list[str]) -> dict[str, tuple[int, str]]:
    """Return the parser directives at the top of the file.

    :param lines: Raw file lines.
    :returns: Lower-cased directive name -> ``(lineno, value)``. Only the
        first occurrence of each directive is kept.
    """
    directives: dict[str, tuple[int, str]] = {}
    for lineno, raw in enumerate(lines, start=1):
        m = _DIRECTIVE_RE.match(raw.strip())
        if not m or m.group(1).lower() not in _KNOWN_DIRECTIVES:
            break
        directives.setdefault(m.group(1).lower(), (lineno, m.group(2)))
    return directives


def _read_instructions(lines: list[str], escape: str) -> list[tuple[int, str]]:
    """Join continuation lines into instructions, skipping heredoc bodies.

    :param lines: Raw file lines.
    :param escape: The continuation character, ``\\`` or a backtick.
    :returns: ``(start_lineno, text)`` for every instruction.
    """
    continuation_re = re.compile(re.escape(escape) + r"[ \t]*$")
    instructions: list[tuple[int, str]] = []
    i = 0
    while i < len(lines):
        # 1. Find the first line of the next instruction.
        first = lines[i].rstrip("\r\n").lstrip()
        start = i + 1
        i += 1
        if not first.strip() or first.startswith("#"):
            continue

        # 2. Append continuation lines as-is, without a separator.
        text, continued = continuation_re.subn("", first)
        while continued and i < len(lines):
            raw = lines[i].rstrip("\r\n")
            i += 1
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            part, continued = continuation_re.subn("", raw)
            text += part
        text = text.strip()
        instructions.append((start, text))

        # 3. Skip the bodies of any heredocs this instruction opens.
        body = _ONBUILD_RE.sub("", text)
        if not _HEREDOC_INSTRUCTION_RE.match(body):
            continue
        for strip_tabs, _, delimiter in _HEREDOC_RE.findall(body):
            while i < len(lines):
                raw = lines[i].rstrip("\r\n")
                i += 1
                if (raw.lstrip("\t") if strip_tabs else raw) == delimiter:
                    break
    return instructions


def _mount_from(option_list: str) -> Optional[str]:
    """Return the ``from=`` value of a ``--mount`` option list, if any.

    BuildKit reads the option list as one CSV record, so a field may be
    quoted (``"from=python:3.11"``).

    :param option_list: The text after ``--mount=``, e.g.
        ``type=bind,from=python:3.11,target=/src``.
    """
    for record in csv.reader([option_list]):
        for field in record:
            key, sep, value = field.partition("=")
            if sep and key.strip().lower() == "from":
                return value.strip()
    return None


def _reference_problem(
    image: str, stages: set[str], allow_stage_refs: bool, allow_index: bool
) -> Optional[str]:
    """Return why an image reference is not pinned, or None when it is.

    :param stages: Lower-cased stage names declared so far in this file.
    :param allow_stage_refs: Whether a bare stage name or ``scratch`` may
        stand in for an image (false for the ``# syntax=`` directive).
    :param allow_index: Whether a bare integer names a stage by index
        (true for ``--from=``, not for ``FROM``).
    """
    if allow_stage_refs:
        lowered = image.lower()
        if lowered == "scratch" or lowered in stages:
            return None
        if allow_index and image.isdigit():
            return None
    if "$" in image:
        return (
            "uses a variable reference which cannot be verified;"
            " write the image reference with an @sha256: digest"
        )
    if "@sha256:" not in image:
        return "is not pinned with @sha256: digest"
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

    def _check_file(self, rel_path: str, lines: list[str]) -> list[Finding]:
        findings: list[Finding] = []
        stages: set[str] = set()

        def check(
            lineno: int,
            label: str,
            image: str,
            allow_stage_refs: bool = True,
            allow_index: bool = True,
        ) -> None:
            problem = _reference_problem(image, stages, allow_stage_refs, allow_index)
            if problem:
                findings.append(
                    Finding(
                        checker=self.name,
                        path=rel_path,
                        line=lineno,
                        message="{} '{}' {}".format(label, image, problem),
                        integrity=True,
                    )
                )

        directives = _read_directives(lines)
        if "syntax" in directives:
            lineno, image = directives["syntax"]
            check(lineno, "# syntax", image, allow_stage_refs=False)
        escape = directives.get("escape", (0, "\\"))[1]
        if escape not in ("\\", "`"):
            escape = "\\"

        for lineno, text in _read_instructions(lines, escape):
            text = _ONBUILD_RE.sub("", text)

            m = _FROM_RE.match(text)
            if m:
                image, alias = m.group(1), m.group(2)
                check(lineno, "FROM", image, allow_index=False)
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
                if ref:
                    check(lineno, label, ref)

        return findings

    def check(self, index: FileIndex, root: str) -> list[Finding]:
        findings: list[Finding] = []

        for dir_path in sorted(index.keys()):
            for fname in sorted(index[dir_path]):
                if not any(fnmatch.fnmatch(fname, p) for p in self.patterns):
                    continue

                full_path = os.path.join(dir_path, fname)
                rel_path = os.path.relpath(full_path, root)

                # utf-8-sig drops a leading byte-order mark, as Docker does;
                # left in place it would hide a FROM or directive on line 1.
                try:
                    with open(
                        full_path, "r", encoding="utf-8-sig", errors="replace"
                    ) as fh:
                        lines = fh.readlines()
                except OSError:
                    continue

                findings.extend(self._check_file(rel_path, lines))

        return findings
