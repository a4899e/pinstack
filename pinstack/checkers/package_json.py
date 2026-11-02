"""package.json checker: enforces exact version pinning in all dependency sections."""

from __future__ import annotations

import json
import os
import re

from pinstack.core import Checker, Finding, FileIndex

# npm accepts many range forms (``*``, ``latest``, ``1.x``, ``1``, hyphen and
# ``||`` ranges, dist-tags, git shorthands). Rather than enumerate the bad
# ones, a version spec passes only if it is one of the forms below; anything
# else is a finding.
#
# Sources:
#   - https://docs.npmjs.com/cli/configuring-npm/package-json#dependencies
#   - https://github.com/npm/node-semver#ranges
#   - https://github.com/npm/npm-package-arg

# Exact semver, optionally written with npm's tolerated "=" or "v" prefix.
_EXACT_SEMVER_RE = re.compile(
    r"^=?v?\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)

# Local references, resolved from the working tree at install time.
_LOCAL_PREFIXES = ("workspace:", "file:", "link:", "portal:", "./", "../", "/", "~/")

# Git sources. These are content-addressed only when the fragment is a full
# 40-hex commit SHA; a branch, tag, or "#semver:" range can move.
_GIT_PREFIXES = (
    "git+",
    "git://",
    "git@",
    "ssh://",
    "github:",
    "gitlab:",
    "bitbucket:",
    "gist:",
)
# "user/repo" GitHub shorthand, with an optional "#ref".
_GITHUB_SHORTHAND_RE = re.compile(r"^[A-Za-z0-9][\w.-]*/[\w.-]+(?:#.*)?$")
_COMMIT_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")

# Remote tarballs carry no integrity field in package.json.
_URL_PREFIXES = ("http://", "https://")

# Comparator prefixes that make a range rather than a version.
_RANGE_PREFIXES = ("^", "~", ">=", "<=", ">", "<")

_NOT_CONTENT_ADDRESSED = "not content-addressed"

_DEP_SECTIONS = (
    "dependencies",
    "devDependencies",
    "peerDependencies",
    "optionalDependencies",
)

# Lock files that provide integrity verification for package.json
_LOCK_FILES = frozenset(["package-lock.json", "yarn.lock", "pnpm-lock.yaml"])


def _is_git_source(version: str) -> bool:
    return version.startswith(_GIT_PREFIXES) or bool(
        _GITHUB_SHORTHAND_RE.match(version)
    )


def _registry_version_problem(version: str) -> str:
    """Return why a registry version spec is not exact, or "" if it is."""
    if _EXACT_SEMVER_RE.match(version):
        return ""
    if version.startswith(_RANGE_PREFIXES):
        return "unpinned version"
    return "not exact semver"


def _is_unpinned(version: object) -> tuple[bool, str]:
    """Return (is_bad, reason) for a dependency version spec.

    ``reason`` is ``"not content-addressed"`` for git and URL sources (an
    integrity problem), ``"unpinned version"`` for comparator ranges, and
    ``"not exact semver"`` for every other spec that is not an exact version.
    """
    if not isinstance(version, str):
        return True, "not exact semver"
    version = version.strip()

    # 1. Local references.
    if version.startswith(_LOCAL_PREFIXES):
        return False, ""

    # 2. Registry alias: npm:<name>@<version>. Scoped names start with "@",
    #    so the version follows the last "@".
    if version.startswith("npm:"):
        target = version[len("npm:") :]
        at_pos = target.rfind("@")
        if at_pos <= 0:
            return True, "not exact semver"
        reason = _registry_version_problem(target[at_pos + 1 :])
        return bool(reason), reason

    # 3. Git sources: only a full commit SHA pins the content.
    if _is_git_source(version):
        _, _, fragment = version.partition("#")
        if _COMMIT_SHA_RE.match(fragment):
            return False, ""
        return True, _NOT_CONTENT_ADDRESSED

    # 4. Remote tarballs.
    if version.startswith(_URL_PREFIXES):
        return True, _NOT_CONTENT_ADDRESSED

    # 5. Registry versions.
    reason = _registry_version_problem(version)
    return bool(reason), reason


def _extract_js_lock_names(full_path: str, lock_filename: str) -> set[str]:
    """Extract package names from a JS lock file."""
    lock_path = os.path.join(os.path.dirname(full_path), lock_filename)
    names: set[str] = set()
    try:
        with open(lock_path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except OSError:
        return names

    if lock_filename == "package-lock.json":
        try:
            data = json.loads(content)
        except (ValueError, TypeError):
            return names
        packages = data.get("packages", {})
        for key in packages:
            if not key:
                continue  # skip root "" entry
            # Strip node_modules/ prefix (possibly nested)
            name = key
            while name.startswith("node_modules/"):
                name = name[len("node_modules/") :]
            if name:
                names.add(name)
    elif lock_filename == "yarn.lock":
        # Non-indented lines ending with : are package headers
        # e.g. "express@4.18.2:" or "express@^4.18.2, express@>=4.0.0:"
        for line in content.splitlines():
            if not line or line[0] in (" ", "\t", "#"):
                continue
            if line.endswith(":"):
                # First entry before comma; extract name before @version
                entry = line.rstrip(":").split(",")[0].strip().strip('"')
                # Package name is everything before the last @ (scoped pkgs have leading @)
                at_pos = entry.rfind("@")
                if at_pos > 0:
                    names.add(entry[:at_pos])
                else:
                    names.add(entry)
    elif lock_filename == "pnpm-lock.yaml":
        # Package entries under packages: like /express@4.18.2: or /express/4.18.2:
        in_packages = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped == "packages:":
                in_packages = True
                continue
            if in_packages:
                # End of packages section: non-indented line that isn't a package entry
                if line and not line[0].isspace():
                    in_packages = False
                    continue
                # Package entry lines start with / after stripping
                if stripped.startswith("/"):
                    entry = stripped.lstrip("/").rstrip(":")
                    # Format: name@version or @scope/name@version
                    # Find last @ that separates name from version
                    at_pos = entry.rfind("@")
                    if at_pos > 0:
                        names.add(entry[:at_pos])
                    else:
                        names.add(entry)

    return names


class PackageJsonChecker(Checker):
    name = "package_json"
    description = (
        "Checks package.json files for unpinned (non-exact) dependency versions"
    )
    patterns: list[str] = [
        "package.json",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
    ]

    def check(self, index: FileIndex, root: str) -> list[Finding]:
        findings: list[Finding] = []

        for dir_path in sorted(index.keys()):
            files = index[dir_path]
            if "package.json" not in files:
                continue

            full_path = os.path.join(dir_path, "package.json")
            rel_path = os.path.relpath(full_path, root)

            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                    data = json.load(fh)
            except (OSError, ValueError):
                continue

            if not isinstance(data, dict):
                continue

            has_deps = False
            for section in _DEP_SECTIONS:
                deps = data.get(section)
                if not deps or not isinstance(deps, dict):
                    continue
                has_deps = True
                for pkg, version in sorted(deps.items()):
                    is_bad, reason = _is_unpinned(version)
                    if is_bad:
                        integrity = reason == _NOT_CONTENT_ADDRESSED
                        if reason == "not exact semver":
                            template = "'{0}' in {1} has version '{3}', which is {2}; use an exact version"
                        else:
                            template = (
                                "'{0}' in {1} has {2} '{3}'; use an exact version"
                            )
                        findings.append(
                            Finding(
                                checker=self.name,
                                path=rel_path,
                                line=0,
                                message=template.format(pkg, section, reason, version),
                                integrity=integrity,
                            )
                        )

            # Check for companion lock file
            found_lock_files = files & _LOCK_FILES
            if has_deps and not found_lock_files:
                findings.append(
                    Finding(
                        checker=self.name,
                        path=rel_path,
                        line=0,
                        message="package.json has dependencies but no lock file (package-lock.json, yarn.lock, or pnpm-lock.yaml)",
                        integrity=True,
                    )
                )
            elif has_deps and found_lock_files:
                # Cross-reference: check that every manifest dep appears in the lock file
                lock_filename = sorted(found_lock_files)[0]
                lock_names = _extract_js_lock_names(full_path, lock_filename)
                if lock_names:
                    missing: list[str] = []
                    for section in _DEP_SECTIONS:
                        deps = data.get(section)
                        if not deps or not isinstance(deps, dict):
                            continue
                        for pkg in sorted(deps.keys()):
                            if pkg not in lock_names:
                                missing.append(pkg)
                    if missing:
                        findings.append(
                            Finding(
                                checker=self.name,
                                path=rel_path,
                                line=0,
                                message="{} is stale: missing {} ({})".format(
                                    lock_filename,
                                    "{} dependency".format(len(missing))
                                    if len(missing) == 1
                                    else "{} dependencies".format(len(missing)),
                                    ", ".join(sorted(missing)),
                                ),
                                integrity=True,
                            )
                        )

        return findings
