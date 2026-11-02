"""GitHub Actions checker: enforces SHA pinning on actions and digests on images.

Workflow files (``.github/workflows/*.yml|yaml``) are checked for:

- every ``uses:`` of a step or of a reusable-workflow job, which must name a
  full 40-character commit SHA (``owner/repo@<sha>``), or for a
  ``docker://`` image an ``@sha256:`` digest; local ``./`` actions are
  skipped;
- job containers (``jobs.<id>.container`` or ``jobs.<id>.container.image``)
  and service containers (``jobs.<id>.services.<name>.image``), which must be
  pinned with an ``@sha256:`` digest.

Action metadata files (``action.yml`` / ``action.yaml`` anywhere, i.e.
composite and Docker actions) are checked for the ``uses:`` of their
composite steps and for a ``runs.image`` that pulls a ``docker://`` image.

The files are read with :mod:`pinstack.yaml_scan`, so flow style
(``- {uses: actions/checkout@v4}``), quoted keys (``"uses":``), values on
the following line and YAML aliases are all seen; text inside a ``run: |``
script is not mistaken for a key.
"""

from __future__ import annotations

import os
from typing import Optional

from pinstack.core import (
    Checker,
    Finding,
    FileIndex,
    image_digest_problem,
    is_commit_sha,
)
from pinstack.yaml_scan import ITEM, Scalar, scalars

_ACTION_FILES = ("action.yml", "action.yaml")


def _is_workflow_path(rel_path: str) -> bool:
    """Return True if rel_path contains .github/workflows/ as consecutive path components."""
    parts = rel_path.replace("\\", "/").split("/")
    for i in range(len(parts) - 1):
        if parts[i] == ".github" and parts[i + 1] == "workflows":
            return True
    return False


def _action_ref_problem(ref: str) -> Optional[str]:
    """Return the finding message for a ``uses:`` value, or None if it is pinned."""
    if ref.startswith("./"):
        return None
    if ref.startswith("docker://"):
        problem = image_digest_problem(ref)
        return "docker action '{}' {}".format(ref, problem) if problem else None
    if "@" not in ref:
        return "action '{}' has no @ ref; pin to a full-length SHA".format(ref)
    action_part, pin = ref.rsplit("@", 1)
    if not is_commit_sha(pin):
        return "action '{}' is not pinned to a full-length SHA (got '{}')".format(
            action_part, pin
        )
    return None


def _image_problem(image: str) -> Optional[str]:
    if "${{" in image:
        return (
            "uses an expression which cannot be verified; write the image"
            " reference with an @sha256: digest"
        )
    return image_digest_problem(image)


def _is_step_uses(path: tuple[str, ...]) -> bool:
    """``...steps[n].uses`` or a reusable workflow's ``jobs.<id>.uses``."""
    if not path or path[-1] != "uses":
        return False
    if len(path) >= 2 and path[-2] == ITEM:
        return True
    return len(path) == 3 and path[0] == "jobs"


def _workflow_image(path: tuple[str, ...]) -> Optional[str]:
    """Return a label if ``path`` is a job or service container image."""
    if len(path) < 3 or path[0] != "jobs":
        return None
    if len(path) == 3 and path[2] == "container":
        return "container image"
    if len(path) == 4 and path[2] == "container" and path[3] == "image":
        return "container image"
    if len(path) == 5 and path[2] == "services" and path[4] == "image":
        return "service '{}' image".format(path[3])
    return None


class GitHubActionsChecker(Checker):
    name = "github_actions"
    description = (
        "Checks GitHub Actions workflows and action metadata for SHA-pinned"
        " actions and digest-pinned container images"
    )
    # Path-qualified so that only workflow files count toward --max-files;
    # every other YAML file in the repository would otherwise be indexed.
    patterns: list[str] = [
        ".github/workflows/*.yml",
        ".github/workflows/*.yaml",
        "*/.github/workflows/*.yml",
        "*/.github/workflows/*.yaml",
        *_ACTION_FILES,
    ]

    def check(self, index: FileIndex, root: str) -> list[Finding]:
        findings: list[Finding] = []

        for dir_path in sorted(index.keys()):
            for fname in sorted(index[dir_path]):
                if not fname.endswith((".yml", ".yaml")):
                    continue
                full_path = os.path.join(dir_path, fname)
                rel_path = os.path.relpath(full_path, root)
                if _is_workflow_path(rel_path):
                    is_action = False
                elif fname in _ACTION_FILES:
                    is_action = True
                else:
                    continue

                try:
                    with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                        content = fh.read()
                except OSError:
                    continue

                for item in scalars(content):
                    message = self._problem(item, is_action)
                    if message:
                        findings.append(
                            Finding(
                                checker=self.name,
                                path=rel_path,
                                line=item.line,
                                message=message,
                                integrity=True,
                            )
                        )

        return findings

    @staticmethod
    def _problem(item: Scalar, is_action: bool) -> Optional[str]:
        path = item.path
        if _is_step_uses(path):
            if item.alias:
                return "uses: '{}' is a YAML alias pinstack could not resolve".format(
                    item.value
                )
            return _action_ref_problem(item.value)

        if is_action:
            if path == ("runs", "image") and item.value.startswith("docker://"):
                problem = image_digest_problem(item.value)
                if problem:
                    return "docker action image '{}' {}".format(item.value, problem)
            return None

        label = _workflow_image(path)
        if label is None or not item.value:
            return None
        if item.alias:
            return "{} '{}' is a YAML alias pinstack could not resolve".format(
                label, item.value
            )
        problem = _image_problem(item.value)
        return "{} '{}' {}".format(label, item.value, problem) if problem else None
