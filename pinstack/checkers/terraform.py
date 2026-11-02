"""Terraform checker: requires .terraform.lock.hcl with h1: hashes for root modules.

Two checks:

1. Every provider block in a ``.terraform.lock.hcl`` must carry an ``h1:`` hash.
2. A root module (a directory of ``*.tf`` files that is applied directly) must
   have a ``.terraform.lock.hcl`` beside it, or ``terraform init`` resolves
   provider versions and checksums afresh on every run.

Only root modules get a lock file; a child module's providers are locked by
the root that calls it. A directory counts as a root module when one of its
``.tf`` files declares or configures providers (``required_providers {``,
``provider "x" {``) or state (``backend "x" {``, ``cloud {``), unless another
scanned ``.tf`` file calls it as a local module (``source = "./..."`` or
``"../..."``). That exemption covers ``modules/`` subdirectories and, in the
standard module layout, a reusable module's root called from ``examples/``.

Source: https://developer.hashicorp.com/terraform/language/files/dependency-lock
"""

from __future__ import annotations

import fnmatch
import os
import re
from typing import Optional

from pinstack.core import Checker, Finding, FileIndex

_LOCK_FILE = ".terraform.lock.hcl"
_ROOT_SIGNAL_RE = re.compile(
    r'^[ \t]*(?:required_providers|provider[ \t]+"[^"]+"|backend[ \t]+"[^"]+"|cloud)'
    r"[ \t]*\{",
    re.M,
)
_MODULE_BLOCK_RE = re.compile(r'^[ \t]*module[ \t]+"[^"]*"[ \t]*\{', re.M)
_LOCAL_SOURCE_RE = re.compile(r'^[ \t]*source[ \t]*=[ \t]*"(\.\.?/[^"]*)"', re.M)
_HEREDOC_START_RE = re.compile(r"<<-?([A-Za-z_][A-Za-z0-9_]*)[ \t]*\n")


def _strip_comments(content: str) -> str:
    """Replace HCL comments and heredoc bodies with spaces.

    Quoted strings and newlines are kept. Comment markers inside a string or
    heredoc are content, and ``/*`` inside
    a ``#`` comment does not open a block comment, so this scans the text
    once rather than applying one regex per comment form. Newlines are kept
    so offsets map to the same line numbers.
    """
    out: list[str] = []
    i, n = 0, len(content)
    while i < n:
        ch = content[i]
        two = content[i : i + 2]
        if ch == "#" or two == "//":
            end = content.find("\n", i)
            end = n if end == -1 else end
            out.append(" " * (end - i))
            i = end
        elif two == "/*":
            end = content.find("*/", i + 2)
            end = n if end == -1 else end + 2
            out.append("".join(c if c == "\n" else " " for c in content[i:end]))
            i = end
        elif ch == '"':
            # A quoted string, ending at the next unescaped quote on the line.
            j = i + 1
            while j < n and content[j] not in ('"', "\n"):
                j += 2 if content[j] == "\\" else 1
            end = min(j + 1, n)
            out.append(content[i:end])
            i = end
        elif two == "<<":
            m = _HEREDOC_START_RE.match(content, i)
            if not m:
                out.append(two)
                i += 2
                continue
            # The body runs to a line holding only the delimiter. It is
            # string content, so it is blanked like a comment.
            end_re = re.compile(r"^[ \t]*" + re.escape(m.group(1)) + r"[ \t]*$", re.M)
            close = end_re.search(content, m.end())
            end = n if close is None else close.end()
            out.append(content[i : m.end()])
            out.append("".join(c if c == "\n" else " " for c in content[m.end() : end]))
            i = end
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _read_tf(path: str) -> str:
    """Return a .tf file's text with comments and heredocs blanked, or ""."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return _strip_comments(fh.read())
    except OSError:
        return ""


def _block_body(content: str, open_brace: int) -> str:
    """Return the text of the block whose ``{`` is at ``open_brace``.

    ``content`` must already have comments stripped. Braces inside quoted
    strings are skipped.
    """
    depth = 0
    i, n = open_brace, len(content)
    while i < n:
        ch = content[i]
        if ch == '"':
            i += 1
            while i < n and content[i] not in ('"', "\n"):
                i += 2 if content[i] == "\\" else 1
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return content[open_brace : i + 1]
        i += 1
    return content[open_brace:]


def _local_module_sources(content: str) -> list[str]:
    """Return the local ``source`` paths of every ``module`` block."""
    sources: list[str] = []
    for m in _MODULE_BLOCK_RE.finditer(content):
        body = _block_body(content, m.end() - 1)
        sources.extend(_LOCAL_SOURCE_RE.findall(body))
    return sources


class TerraformChecker(Checker):
    name = "terraform"
    description = (
        "Checks Terraform root modules have a .terraform.lock.hcl with h1: hashes"
    )
    patterns: list[str] = [_LOCK_FILE, "*.tf"]

    def _check_root_modules(self, index: FileIndex, root: str) -> list[Finding]:
        """Report root-module directories with no .terraform.lock.hcl."""
        # 1. Find each directory's first root-module signal and every local
        #    module it calls.
        signals: dict[str, tuple[str, int]] = {}  # dir -> (file, line)
        called: set[str] = set()
        for dir_path in sorted(index.keys()):
            for fname in sorted(index[dir_path]):
                if not fname.endswith(".tf"):
                    continue
                content = _read_tf(os.path.join(dir_path, fname))
                m = _ROOT_SIGNAL_RE.search(content)
                if m and dir_path not in signals:
                    lineno = content.count("\n", 0, m.start()) + 1
                    signals[dir_path] = (fname, lineno)
                for source in _local_module_sources(content):
                    called.add(os.path.normpath(os.path.join(dir_path, source)))

        # 2. A root module without a lock file is a finding.
        findings: list[Finding] = []
        for dir_path, (fname, lineno) in sorted(signals.items()):
            if _LOCK_FILE in index[dir_path]:
                continue
            if os.path.normpath(dir_path) in called:
                continue
            findings.append(
                Finding(
                    checker=self.name,
                    path=os.path.relpath(os.path.join(dir_path, fname), root),
                    line=lineno,
                    message=(
                        "Terraform root module has no .terraform.lock.hcl; run"
                        " 'terraform init' and commit the lock file"
                    ),
                    integrity=True,
                )
            )
        return findings

    def check(self, index: FileIndex, root: str) -> list[Finding]:
        findings: list[Finding] = self._check_root_modules(index, root)

        for dir_path in sorted(index.keys()):
            for fname in sorted(index[dir_path]):
                if not fnmatch.fnmatch(fname, _LOCK_FILE):
                    continue

                full_path = os.path.join(dir_path, fname)
                rel_path = os.path.relpath(full_path, root)

                try:
                    with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                        lines = fh.readlines()
                except OSError:
                    continue

                in_provider = False
                provider_name: Optional[str] = None
                provider_line = 0
                in_hashes = False
                has_h1 = False

                for lineno, raw_line in enumerate(lines, start=1):
                    line = raw_line.strip()

                    # Detect provider block start: provider "registry.terraform.io/..."
                    if line.startswith('provider "') and line.endswith("{"):
                        # Flush previous provider if any
                        if in_provider and provider_name is not None and not has_h1:
                            findings.append(
                                Finding(
                                    checker="terraform",
                                    path=rel_path,
                                    line=provider_line,
                                    message="provider '{}' is missing h1: hash in lock file".format(
                                        provider_name
                                    ),
                                    integrity=True,
                                )
                            )
                        in_provider = True
                        in_hashes = False
                        has_h1 = False
                        provider_line = lineno
                        # Extract short name from "registry.terraform.io/hashicorp/aws"
                        try:
                            raw = line.split('"')[
                                1
                            ]  # registry.terraform.io/hashicorp/aws
                            provider_name = raw.split("/")[-1]
                        except IndexError:
                            provider_name = line
                        continue

                    if not in_provider:
                        continue

                    # Closing brace at top level ends the provider block
                    if line == "}":
                        in_hashes = False
                        if provider_name is not None and not has_h1:
                            findings.append(
                                Finding(
                                    checker="terraform",
                                    path=rel_path,
                                    line=provider_line,
                                    message="provider '{}' is missing h1: hash in lock file".format(
                                        provider_name
                                    ),
                                    integrity=True,
                                )
                            )
                        in_provider = False
                        provider_name = None
                        has_h1 = False
                        continue

                    if line.startswith("hashes = ["):
                        in_hashes = True
                        continue

                    if in_hashes and "]" in line and not line.startswith('"'):
                        in_hashes = False
                        continue

                    if in_hashes and '"h1:' in line:
                        has_h1 = True

                # Flush last provider block if file ended without closing brace
                if in_provider and provider_name is not None and not has_h1:
                    findings.append(
                        Finding(
                            checker="terraform",
                            path=rel_path,
                            line=provider_line,
                            message="provider '{}' is missing h1: hash in lock file".format(
                                provider_name
                            ),
                            integrity=True,
                        )
                    )

        return findings
