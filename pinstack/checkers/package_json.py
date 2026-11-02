"""package.json checker: enforces exact version pinning in all dependency sections."""

from __future__ import annotations

import json
import os
import re

from pinstack.core import Checker, Finding, FileIndex, is_commit_sha

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
