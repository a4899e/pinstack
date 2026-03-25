"""Terraform checker: enforces h1: hashes in .terraform.lock.hcl provider blocks."""

import fnmatch
import os
from typing import Dict, Set

from pinstack.core import Checker, Finding

FileIndex = Dict[str, Set[str]]


class TerraformChecker(Checker):
    name = "terraform"
    description = "Checks .terraform.lock.hcl for provider blocks missing h1: hashes"
    patterns = [".terraform.lock.hcl"]  # type: List[str]

    def check(self, index, root):
        # type: (FileIndex, str) -> List[Finding]
        findings = []  # type: List[Finding]

        for dir_path in sorted(index.keys()):
            for fname in sorted(index[dir_path]):
                if not fnmatch.fnmatch(fname, ".terraform.lock.hcl"):
                    continue

                full_path = os.path.join(dir_path, fname)
                rel_path = os.path.relpath(full_path, root)

                try:
                    with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                        lines = fh.readlines()
                except OSError:
                    continue

                in_provider = False
                provider_name = None  # type: Optional[str]
                provider_line = 0
                in_hashes = False
                has_h1 = False

                for lineno, raw_line in enumerate(lines, start=1):
                    line = raw_line.strip()

                    # Detect provider block start: provider "registry.terraform.io/..."
                    if line.startswith('provider "') and line.endswith("{"):
                        # Flush previous provider if any
                        if in_provider and provider_name is not None and not has_h1:
                            findings.append(Finding(
                                checker="terraform",
                                path=rel_path,
                                line=provider_line,
                                message="provider '{}' is missing h1: hash in lock file".format(provider_name),
                            ))
                        in_provider = True
                        in_hashes = False
                        has_h1 = False
                        provider_line = lineno
                        # Extract short name from "registry.terraform.io/hashicorp/aws"
                        try:
                            raw = line.split('"')[1]  # registry.terraform.io/hashicorp/aws
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
                            findings.append(Finding(
                                checker="terraform",
                                path=rel_path,
                                line=provider_line,
                                message="provider '{}' is missing h1: hash in lock file".format(provider_name),
                            ))
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
                    findings.append(Finding(
                        checker="terraform",
                        path=rel_path,
                        line=provider_line,
                        message="provider '{}' is missing h1: hash in lock file".format(provider_name),
                    ))

        return findings
