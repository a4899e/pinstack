"""Docker Compose checker: enforces digest pinning on image references.

Every ``image:`` value in a compose file must end in ``@sha256:`` and 64
lowercase hex characters. The file is read with :mod:`pinstack.yaml_scan`,
so flow style (``services: {web: {image: nginx:1.25}}``), quoted keys and
values, and images set through a YAML anchor (``x-base: &base`` then
``<<: *base``) are all seen. Inside a service (or an ``x-`` extension
block), ``image`` keys under ``labels``, ``environment``, ``annotations``
and ``build.args`` are data, not images, and are skipped; the service's own
name never counts, so a service called ``labels`` is still checked.

Compose interpolates ``$VAR`` / ``${VAR}`` from the environment, so a
reference whose digest is not written out literally is reported as
unverifiable; a variable before a literal digest (``${REGISTRY}/app@sha256:...``)
is fine, since the digest still names the content.
"""

from __future__ import annotations

import fnmatch
import os

from pinstack.core import (
    IMAGE_VARIABLE_PROBLEM,
    Checker,
    Finding,
    FileIndex,
    image_digest_problem,
)
from pinstack.yaml_scan import UNRESOLVED_ALIAS, scalars

_DATA_KEYS = frozenset(["labels", "environment", "annotations", "args"])


def _is_data(path: tuple[str, ...]) -> bool:
    """Return True if an ``image`` key at ``path`` is data, not an image.

    Only keys below the service (``services.<name>.…``) or below a
    top-level block (``x-base.…``) are looked at, so a service's own name
    cannot hide it.
    """
    inner = path[2:-1] if path[:1] == ("services",) else path[1:-1]
    return bool(_DATA_KEYS.intersection(inner))


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
                    with open(
                        full_path, "r", encoding="utf-8-sig", errors="replace"
                    ) as fh:
                        content = fh.read()
                except OSError:
                    continue

                for item in scalars(content):
                    if item.key != "image" or _is_data(item.path):
                        continue
                    image = item.value
                    if item.alias:
                        problem = UNRESOLVED_ALIAS
                    else:
                        problem = image_digest_problem(image)
                        if problem and "$" in image.replace("$$", ""):
                            problem = IMAGE_VARIABLE_PROBLEM
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
