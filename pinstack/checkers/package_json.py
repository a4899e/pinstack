"""package.json checker: enforces exact version pinning in all dependency sections."""

from __future__ import annotations

import json
import os
import re

from pinstack.core import (
    Checker,
    Finding,
    FileIndex,
    describe_missing,
    empty_lock_finding,
    is_commit_sha,
)

# npm accepts many range forms (``*``, ``latest``, ``1.x``, ``1``, hyphen and
# ``||`` ranges, dist-tags, git shorthands). Rather than enumerate the bad
# ones, a version spec passes only if it is one of the forms below; anything
# else is a finding. The same rules apply to the ``overrides`` (npm),
# ``resolutions`` (yarn) and ``pnpm.overrides`` maps, which replace the
# version a dependency resolves to.
#
# Sources:
#   - https://docs.npmjs.com/cli/configuring-npm/package-json#dependencies
#   - https://github.com/npm/node-semver#ranges
#   - https://github.com/npm/npm-package-arg

# Exact semver, optionally written with npm's tolerated "=" or "v" prefix.
# Prerelease and build identifiers are non-empty and dot-separated; anything
# looser (e.g. "1.2.3-..") is not a version to npm and resolves as a dist-tag.
_IDENTIFIERS = r"[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*"
_EXACT_SEMVER_RE = re.compile(
    r"^=?v?(?P<core>[0-9]+\.[0-9]+\.[0-9]+)(?:-{0})?(?:\+{0})?$".format(_IDENTIFIERS)
)

# Local references, resolved from the working tree at install time.
_LOCAL_PREFIXES = (
    "workspace:",
    "file:",
    "link:",
    "portal:",
    "./",
    "../",
    ".\\",
    "..\\",
    "/",
    "~/",
)
_LOCAL_EXACT = frozenset([".", ".."])
_WINDOWS_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")

# Git sources. These are content-addressed only when the fragment selects a
# full commit SHA; a branch, tag, or "#semver:" range can move.
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
# An https URL to a repository on a host npm resolves as git, not a tarball.
_HOSTED_GIT_URL_RE = re.compile(
    r"^https?://(?:www\.)?(?:github\.com|gitlab\.com|bitbucket\.org)"
    r"/[\w.-]+/[\w.-]+/?(?:#.*)?$"
)

# Remote tarballs carry no integrity field in package.json.
_URL_PREFIXES = ("http://", "https://")

# Comparator prefixes and operators that make a range rather than a version.
_RANGE_PREFIXES = ("^", "~", ">=", "<=", ">", "<")
_RANGE_OPERATORS = ("||", " - ")

# Finding reasons. Each selects a message template below.
_UNPINNED = "unpinned version"
_NOT_EXACT = "not exact semver"
_NOT_CONTENT_ADDRESSED = "not content-addressed"

_MESSAGE_TEMPLATES = {
    _UNPINNED: "'{pkg}' in {section} has unpinned version '{version}'; use an exact version",
    _NOT_EXACT: "'{pkg}' in {section} has version '{version}', which is not exact semver; use an exact version",
    _NOT_CONTENT_ADDRESSED: "'{pkg}' in {section} has not content-addressed '{version}'; use an exact version",
}

_DEP_SECTIONS = (
    "dependencies",
    "devDependencies",
    "peerDependencies",
    "optionalDependencies",
)

_PNPM_V5_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+")

# Lock files that provide integrity verification for package.json
_LOCK_FILES = frozenset(["package-lock.json", "yarn.lock", "pnpm-lock.yaml"])


def _is_local(version: str) -> bool:
    return (
        version.startswith(_LOCAL_PREFIXES)
        or version in _LOCAL_EXACT
        or bool(_WINDOWS_PATH_RE.match(version))
    )


def _is_git_source(version: str) -> bool:
    return (
        version.startswith(_GIT_PREFIXES)
        or bool(_GITHUB_SHORTHAND_RE.match(version))
        or bool(_HOSTED_GIT_URL_RE.match(version))
    )


def _git_fragment_pins_commit(fragment: str) -> bool:
    """Return True if a git spec's ``#fragment`` is one full commit SHA.

    An optional ``::path:...`` suffix is allowed: ``:`` cannot appear in a
    git ref name, so the part before it can only resolve as a commit. Other
    fragment syntaxes are rejected because their meaning depends on the
    package manager. Yarn reads ``#commit=<sha>`` as a commit, but npm
    passes it to git as a ref name, and ``=`` and ``&`` are legal in branch
    names, so a branch called ``commit=<sha>`` could move.
    """
    return is_commit_sha(fragment.split("::", 1)[0])


# node-semver rejects versions longer than this, or with a major, minor or
# patch above Number.MAX_SAFE_INTEGER; npm then resolves the spec as a
# dist-tag, which can move.
_SEMVER_MAX_LENGTH = 256
_MAX_SAFE_INTEGER = 2**53 - 1


def _is_exact_semver(version: str) -> bool:
    m = _EXACT_SEMVER_RE.match(version)
    if not m or len(version) > _SEMVER_MAX_LENGTH:
        return False
    return all(int(part) <= _MAX_SAFE_INTEGER for part in m.group("core").split("."))


def _registry_version_problem(version: str) -> str:
    """Return the reason a registry version spec is not exact, or ""."""
    if _is_exact_semver(version):
        return ""
    if version.startswith(_RANGE_PREFIXES) or any(
        op in version for op in _RANGE_OPERATORS
    ):
        return _UNPINNED
    return _NOT_EXACT


def _is_unpinned(version: object) -> tuple[bool, str]:
    """Return (is_bad, reason) for a dependency version spec.

    ``reason`` is ``_NOT_CONTENT_ADDRESSED`` for git and URL sources (an
    integrity problem), ``_UNPINNED`` for ranges, and ``_NOT_EXACT`` for
    every other spec that is not an exact version.
    """
    if not isinstance(version, str):
        return True, _NOT_EXACT
    version = version.strip()

    # 1. Local references.
    if _is_local(version):
        return False, ""

    # 2. Registry alias: npm:<name>@<version>, or yarn's npm:<version> for
    #    the same package. Scoped names start with "@", so the version
    #    follows the last "@".
    if version.startswith("npm:"):
        target = version[len("npm:") :]
        at_pos = target.rfind("@")
        reason = _registry_version_problem(
            target[at_pos + 1 :] if at_pos > 0 else target
        )
        return bool(reason), reason

    # 3. Git sources: only a full commit SHA pins the content.
    if _is_git_source(version):
        _, _, fragment = version.partition("#")
        if _git_fragment_pins_commit(fragment):
            return False, ""
        return True, _NOT_CONTENT_ADDRESSED

    # 4. Remote tarballs.
    if version.startswith(_URL_PREFIXES):
        return True, _NOT_CONTENT_ADDRESSED

    # 5. Registry versions.
    reason = _registry_version_problem(version)
    return bool(reason), reason


def _override_specs(
    overrides: object, path: tuple[str, ...] = ()
) -> list[tuple[str, object]]:
    """Flatten an npm ``overrides`` tree into ``(key path, spec)`` pairs.

    Nested objects scope an override to a parent package; their ``"."`` key
    is the parent's own version. Non-object, non-string values are returned
    so the caller reports them.
    """
    if not isinstance(overrides, dict):
        return [(" > ".join(path), overrides)] if path else []
    specs: list[tuple[str, object]] = []
    for key in sorted(overrides):
        value = overrides[key]
        key_path = path if key == "." else path + (key,)
        if isinstance(value, dict):
            specs.extend(_override_specs(value, key_path))
        else:
            specs.append((" > ".join(key_path), value))
    return specs


def _is_override_unpinned(spec: object, allow_removal: bool) -> tuple[bool, str]:
    """Like ``_is_unpinned``, for an override or resolution value.

    ``$name`` defers to the version of the direct dependency ``name``, which
    is checked on its own. pnpm also accepts ``-`` to remove a dependency.
    """
    if isinstance(spec, str):
        stripped = spec.strip()
        if stripped.startswith("$") or (allow_removal and stripped == "-"):
            return False, ""
    return _is_unpinned(spec)


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
        if not isinstance(data, dict):
            return names
        # lockfileVersion 2 and 3 list "node_modules/<name>" paths under
        # "packages"; lockfileVersion 1 (and v2, for compatibility) maps
        # top-level names under "dependencies".
        packages = data.get("packages")
        if isinstance(packages, dict):
            for key in packages:
                if not key:
                    continue  # skip root "" entry
                # The installed name is the part after the last node_modules/
                name = key.rsplit("node_modules/", 1)[-1]
                if name:
                    names.add(name)
        dependencies = data.get("dependencies")
        if isinstance(dependencies, dict):
            names.update(k for k in dependencies if k)
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
        # Entries under "packages:" are keyed by name and version:
        #   v5:      /express/4.18.2:   /react-dom/18.2.0_react@18.2.0:
        #   v6-v8:   /express@4.18.2:   /@scope/pkg@1.0.0(peer@1.0.0):
        #   v9:      express@4.18.2:    '@scope/pkg@1.0.0':
        in_packages = False
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped:
                continue  # blank lines separate entries
            if line[0] not in (" ", "\t"):
                in_packages = stripped == "packages:"
                continue
            if not in_packages or not stripped.endswith(":"):
                continue
            # Package keys are the least-indented lines in the section.
            if len(line) - len(line.lstrip()) != 2:
                continue
            entry = stripped[:-1].strip("'\"").lstrip("/").split("(", 1)[0]
            # v5 "name/version[_peer@x]": the last segment is the version.
            name, sep, last = entry.rpartition("/")
            if sep and _PNPM_V5_VERSION_RE.match(last):
                names.add(name)
                continue
            at_pos = entry.rfind("@")
            if at_pos > 0:
                names.add(entry[:at_pos])
            elif entry:
                names.add(entry)

    return names


_YARN_LOCK_MARKERS = re.compile(r"^(?:# yarn lockfile v1|__metadata:)", re.M)
_PNPM_LOCK_MARKER = re.compile(r"^lockfileVersion:", re.M)


def _js_lock_recognised(full_path: str, lock_filename: str) -> bool:
    """Return True if a lock file is in a format its package manager writes.

    A recognised lock may legitimately list no packages, but only when the
    manifest declares nothing that needs a locked package (only local links,
    or only optional peers that were not installed). An empty or
    unrecognised file never may.
    """
    lock_path = os.path.join(os.path.dirname(full_path), lock_filename)
    try:
        with open(lock_path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except OSError:
        return False
    if lock_filename == "package-lock.json":
        try:
            data = json.loads(content)
        except (ValueError, TypeError):
            return False
        if not isinstance(data, dict):
            return False
        version = data.get("lockfileVersion")
        if not isinstance(version, int):
            return False
        # From lockfileVersion 2 on, npm always writes "packages" with at
        # least the root ("") entry.
        return version < 2 or isinstance(data.get("packages"), dict)
    if lock_filename == "yarn.lock":
        return bool(_YARN_LOCK_MARKERS.search(content))
    return bool(_PNPM_LOCK_MARKER.search(content))


def _needs_lock_entry(data: dict, section: str, pkg: str, spec: object) -> bool:
    """Return True if a declared dependency must appear in the lock file.

    Local references are resolved from the tree, and an optional peer
    (``peerDependenciesMeta.<pkg>.optional``) is not installed, so neither
    has to be listed as a locked package.
    """
    if isinstance(spec, str) and _is_local(spec.strip()):
        return False
    if section == "peerDependencies":
        meta = data.get("peerDependenciesMeta")
        entry = meta.get(pkg) if isinstance(meta, dict) else None
        if isinstance(entry, dict) and entry.get("optional") is True:
            return False
    return True


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
            specs: list[tuple[str, str, object, bool]] = []
            for section in _DEP_SECTIONS:
                deps = data.get(section)
                if not deps or not isinstance(deps, dict):
                    continue
                has_deps = True
                for pkg, version in sorted(deps.items()):
                    specs.append((section, pkg, version, False))
            for pkg, version in _override_specs(data.get("overrides")):
                specs.append(("overrides", pkg, version, False))
            resolutions = data.get("resolutions")
            if isinstance(resolutions, dict):
                for pkg, version in sorted(resolutions.items()):
                    specs.append(("resolutions", pkg, version, False))
            pnpm = data.get("pnpm")
            pnpm_overrides = pnpm.get("overrides") if isinstance(pnpm, dict) else None
            if isinstance(pnpm_overrides, dict):
                for pkg, version in sorted(pnpm_overrides.items()):
                    specs.append(("pnpm.overrides", pkg, version, True))

            for section, pkg, version, allow_removal in specs:
                if section in _DEP_SECTIONS:
                    is_bad, reason = _is_unpinned(version)
                else:
                    is_bad, reason = _is_override_unpinned(version, allow_removal)
                if not is_bad:
                    continue
                findings.append(
                    Finding(
                        checker=self.name,
                        path=rel_path,
                        line=0,
                        message=_MESSAGE_TEMPLATES[reason].format(
                            pkg=pkg, section=section, version=version
                        ),
                        integrity=reason == _NOT_CONTENT_ADDRESSED,
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
                # Cross-reference: every manifest dep must appear in some lock
                # file. A lock that yields no packages is reported unless it is
                # in a recognised format and the manifest declares nothing that
                # needs a locked package; another lock covering the deps does
                # not excuse it. The cross-reference still runs against the
                # rest.
                needed = [
                    pkg
                    for section in _DEP_SECTIONS
                    if isinstance(data.get(section), dict)
                    for pkg, spec in sorted(data[section].items())
                    if _needs_lock_entry(data, section, pkg, spec)
                ]
                lock_filenames = sorted(found_lock_files)
                lock_names: set[str] = set()
                for lock_filename in lock_filenames:
                    names = _extract_js_lock_names(full_path, lock_filename)
                    if not names and (
                        needed or not _js_lock_recognised(full_path, lock_filename)
                    ):
                        findings.append(
                            empty_lock_finding(
                                self.name,
                                os.path.relpath(
                                    os.path.join(dir_path, lock_filename), root
                                ),
                            )
                        )
                    lock_names |= names
                missing = [pkg for pkg in needed if pkg not in lock_names]
                if missing:
                    findings.append(
                        Finding(
                            checker=self.name,
                            path=rel_path,
                            line=0,
                            message="{} {} stale: {}".format(
                                ", ".join(lock_filenames),
                                "is" if len(lock_filenames) == 1 else "are",
                                describe_missing(missing),
                            ),
                            integrity=True,
                        )
                    )

        return findings
