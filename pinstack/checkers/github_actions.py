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
script is not mistaken for a key. An alias of an anchored step, job or
mapping is checked where it is used. An alias the reader could not resolve
is reported wherever it could stand for something this checker checks (a
job, a step, a container or a service), since what it stands for is unknown.
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
from pinstack.yaml_scan import (
    ITEM,
    UNRESOLVED_ALIAS,
    Scalar,
    YamlTooDeep,
    scalars,
)

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
    """``...steps[n].uses`` or a reusable workflow's ``jobs.<id>.uses``.

    A ``uses`` key elsewhere (a matrix variable, an action input) is data.
    """
    if not path or path[-1] != "uses":
        return False
    if path[-3:-1] == ("steps", ITEM):
        return True
    return len(path) == 3 and path[0] == "jobs"


def _without_merge_keys(path: tuple[str, ...]) -> tuple[str, ...]:
    """Drop ``<<`` merge keys (and ``<<: [...]`` items): they merge in place."""
    out: list[str] = []
    for i, name in enumerate(path):
        if name == "<<" or (name == ITEM and i > 0 and path[i - 1] == "<<"):
            continue
        out.append(name)
    return tuple(out)


def _holds_checked_values(path: tuple[str, ...], is_action: bool) -> bool:
    """True if a node at ``path`` could contain a ``uses`` or an image we check."""
    path = _without_merge_keys(path)
    if is_action:
        return path in ((), ("runs",), ("runs", "steps"), ("runs", "steps", ITEM))
    if path in ((), ("jobs",)):
        return True
    if len(path) < 2 or path[0] != "jobs":
        return False
    rest = path[2:]
    if rest in ((), ("steps",), ("steps", ITEM), ("container",), ("services",)):
        return True
    return len(rest) == 2 and rest[0] == "services"


def _workflow_image(path: tuple[str, ...]) -> Optional[str]:
    """Return a label if ``path`` is a job or service container image."""
    if len(path) < 3 or path[0] != "jobs":
        return None
    if path[2:] in (("container",), ("container", "image")):
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
                    with open(
                        full_path, "r", encoding="utf-8-sig", errors="replace"
                    ) as fh:
                        content = fh.read()
                except OSError:
                    continue

                try:
                    items = scalars(content)
                except YamlTooDeep as exc:
                    findings.append(
                        Finding(
                            checker=self.name,
                            path=rel_path,
                            line=exc.line,
                            message="{} has {}, which pinstack cannot read".format(
                                fname, exc
                            ),
                            integrity=True,
                        )
                    )
                    continue
                for item in items:
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
                return "uses: '{}' {}".format(item.value, UNRESOLVED_ALIAS)
            return _action_ref_problem(item.value)

        if is_action and path == ("runs", "image"):
            if item.alias:
                return "docker action image '{}' {}".format(
                    item.value, UNRESOLVED_ALIAS
                )
            if item.value.startswith("docker://"):
                problem = image_digest_problem(item.value)
                if problem:
                    return "docker action image '{}' {}".format(item.value, problem)
            return None

        label = None if is_action else _workflow_image(path)
        if label is not None and item.value:
            if item.alias:
                return "{} '{}' {}".format(label, item.value, UNRESOLVED_ALIAS)
            problem = _image_problem(item.value)
            return "{} '{}' {}".format(label, item.value, problem) if problem else None

        # An alias standing for a whole job, step, container or service.
        if item.alias and _holds_checked_values(path, is_action):
            return "'{}' at {} {}".format(
                item.value, ".".join(path) or "the top level", UNRESOLVED_ALIAS
            )
        return None
