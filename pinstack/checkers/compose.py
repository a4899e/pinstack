"""Docker Compose checker: enforces digest pinning on image references.

Every ``image:`` value in a compose file must end in ``@sha256:`` and 64
lowercase hex characters. The file is read with :mod:`pinstack.yaml_scan`,
so flow style (``services: {web: {image: nginx:1.25}}``), quoted keys and
values, and images set through a YAML anchor (``x-base: &base`` then
``<<: *base``) are all seen. ``image`` keys under ``labels``,
``environment`` and ``annotations`` are data, not images, and are skipped.

Compose interpolates ``$VAR`` / ``${VAR}`` from the environment, so a
reference whose digest is not written out literally is reported as
unverifiable; a variable before a literal digest (``${REGISTRY}/app@sha256:...``)
is fine, since the digest still names the content.
"""

from __future__ import annotations

import fnmatch
import os

from pinstack.core import Checker, Finding, FileIndex, image_digest_problem
from pinstack.yaml_scan import scalars

_DATA_KEYS = frozenset(["labels", "environment", "annotations"])


class ComposeChecker(Checker):
    name = "compose"
    description = (
        "Checks docker-compose files for digest (@sha256:) pinning on image references"
    )
    patterns: list[str] = [
        "docker-compose*.yml",
        "docker-compose*.yaml",
        "compose*.yml",
        "compose*.yaml",
    ]

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
                        content = fh.read()
                except OSError:
                    continue

                for item in scalars(content):
                    if item.key != "image" or _DATA_KEYS.intersection(item.path[:-1]):
                        continue
                    image = item.value
                    if item.alias:
                        problem = "is a YAML alias pinstack could not resolve"
                    else:
                        problem = image_digest_problem(image)
                        if problem and "$" in image.replace("$$", ""):
                            problem = (
                                "uses a variable reference which cannot be verified;"
                                " write the image reference with an @sha256: digest"
                            )
                    if problem is None:
                        continue
                    findings.append(
                        Finding(
                            checker=self.name,
                            path=rel_path,
                            line=item.line,
                            message="image '{}' {}".format(image, problem),
                            integrity=True,
                        )
                    )

        return findings
