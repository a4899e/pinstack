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
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_ROOT_SIGNAL_RE = re.compile(
    r'^[ \t]*(?:required_providers|provider[ \t]+"[^"]+"|backend[ \t]+"[^"]+"|cloud)'
    r"[ \t]*\{",
    re.M,
)
_LOCAL_SOURCE_RE = re.compile(r'^[ \t]*source[ \t]*=[ \t]*"(\.\.?/[^"]*)"', re.M)


def _read_tf(path: str) -> str:
    """Return a .tf file's text with comments removed, or "" if unreadable."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except OSError:
        return ""
    # Blank out block comments line-for-line so line numbers still hold.
    content = _BLOCK_COMMENT_RE.sub(lambda m: "\n" * m.group(0).count("\n"), content)
    lines = []
    for line in content.split("\n"):
        stripped = line.lstrip()
        lines.append("" if stripped.startswith(("#", "//")) else line)
    return "\n".join(lines)


class TerraformChecker(Checker):
    name = "terraform"
    description = "Checks .terraform.lock.hcl for provider blocks missing h1: hashes"
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
                for source in _LOCAL_SOURCE_RE.findall(content):
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
