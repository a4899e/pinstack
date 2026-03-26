"""Pyproject checker: enforces == pinning in [project], [dependency-groups], and [tool.poetry] dependencies."""

from __future__ import annotations

import fnmatch
import os
import re

from pinstack.core import Checker, Finding, FileIndex, validate_url_fragment_hashes

# Sections we care about (PEP 621 + PEP 735)
_SECTION_PROJECT = "project"
_SECTION_OPTIONAL = "project.optional-dependencies"
_SECTION_DEP_GROUPS = "dependency-groups"

# Poetry section prefixes
_POETRY_DEPS_SECTION = "tool.poetry.dependencies"
_POETRY_DEV_DEPS_SECTION = "tool.poetry.dev-dependencies"
_POETRY_GROUP_DEPS_RE = re.compile(r'^tool\.poetry\.group\.[^.]+\.dependencies$')

# Matches a TOML section header like [project] or [project.optional-dependencies]
_SECTION_RE = re.compile(r'^\s*\[([^\]]+)\]')

# Matches a key = [ start of an array assignment (bare or quoted keys)
_ARRAY_START_RE = re.compile(r'^\s*(?:"([^"]+)"|\'([^\']+)\'|([A-Za-z0-9_.\-]+))\s*=\s*\[')

# Operators that are NOT ==
_BAD_OPERATORS_RE = re.compile(r'(!=|~=|>=|<=|>(?!=)|<(?!=))')

# Matches a PEP 508 dependency specifier (simplified):
#   name[extras] operator version ; marker
# We only need: name + first operator + version
_DEP_RE = re.compile(
    r'^([A-Za-z0-9_.\-]+(?:\[[A-Za-z0-9_.,\s]+\])?)'   # package name + optional extras
    r'\s*'
    r'(==|!=|~=|>=|<=|>|<)?'                              # optional operator
    r'\s*'
    r'([^\s;,\]]*)'                                        # optional version
)


def _extract_string_value(raw: str) -> str:
    """Strip surrounding quotes from a TOML string value."""
    s = raw.strip().strip(",").strip()
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    return s


def _strip_inline_comment(line: str) -> str:
    """Remove trailing inline comment from a TOML line (outside of strings)."""
    # Simple approach: find ' #' that is not inside a quoted string
    in_single = False
    in_double = False
    for i, ch in enumerate(line):
        if ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '#' and not in_single and not in_double:
            return line[:i]
    return line


def extract_dependency_arrays(content: str) -> list[tuple[list[str], str, str]]:
    """
    Parse pyproject.toml content with a state machine and extract dependency arrays.

    Returns a list of (deps, label, section) tuples where:
      deps    -- list of dependency specifier strings (quotes stripped)
      label   -- human-readable label for the array (e.g. "dependencies", "dev")
      section -- the TOML section name (e.g. "project", "tool.hatch.envs.default")

    Under [project], only the "dependencies" key is returned (classifiers,
    requires, etc. are skipped).  Under [project.optional-dependencies] and
    [dependency-groups], any key is a dep group.  For all other sections,
    arrays are returned if "dependencies" appears in either the key or the
    section name (catches tool.hatch.envs.*.dependencies,
    tool.pdm.dev-dependencies, etc.).  Bare top-level arrays before any
    section header are ignored.
    """
    results: list[tuple[list[str], str]] = []
    current_section = ""   # current TOML section name
    in_dep_array = False   # are we inside a target dependency array?
    current_deps: list[str] = []
    current_label = ""     # label for the current array
    array_depth = 0        # bracket nesting depth (to handle inline arrays properly)

    lines = content.splitlines()

    for line in lines:
        stripped = line.strip()

        # --- Handle section headers ---
        section_match = _SECTION_RE.match(stripped)
        if section_match:
            # Close any open array (shouldn't happen in well-formed TOML, but be safe)
            if in_dep_array and current_deps is not None:
                results.append((current_deps, current_label))
                in_dep_array = False
                current_deps = []
                current_label = ""
                array_depth = 0
            current_section = section_match.group(1).strip()
            continue

        # --- If inside a dependency array, collect items ---
        if in_dep_array:
            clean = _strip_inline_comment(line)

            # Check if this line closes our array (a bare ']' or '],')
            # We detect close by looking for an unbalanced ']' at depth 1
            open_count = clean.count("[")
            close_count = clean.count("]")
            new_depth = array_depth + open_count - close_count

            if new_depth <= 0:
                # The closing bracket of our outermost array is on this line
                # Parse any items on the same line before the closing ]
                close_pos = clean.rfind("]")
                before_close = clean[:close_pos] if close_pos >= 0 else clean
                for item in _parse_array_items_from_line(before_close):
                    val = _extract_string_value(item)
                    if val:
                        current_deps.append(val)
                results.append((current_deps, current_label))
                in_dep_array = False
                current_deps = []
                current_label = ""
                array_depth = 0
                continue

            array_depth = new_depth
            # Parse items from this continuation line
            for item in _parse_array_items_from_line(clean):
                val = _extract_string_value(item)
                if val:
                    current_deps.append(val)
            continue

        # --- Look for array start ---
        array_match = _ARRAY_START_RE.match(stripped)
        if not array_match:
            continue

        key = array_match.group(1) or array_match.group(2) or array_match.group(3)

        # Determine if this is a dependency array we care about.
        # - Must be inside a section (no bare top-level arrays).
        # - Under [project], only the "dependencies" key is a dep array
        #   (skip classifiers, requires, etc.).
        # - Under [project.optional-dependencies] and [dependency-groups],
        #   any key is a dep group name.
        # - For all other sections, accept if "dependencies" appears in
        #   the key OR the section name (catches tool.hatch.envs.*.dependencies,
        #   tool.pdm.dev-dependencies, etc.).
        if not current_section:
            continue
        elif current_section == _SECTION_PROJECT:
            if key != "dependencies":
                continue
        elif current_section in (_SECTION_OPTIONAL, _SECTION_DEP_GROUPS):
            pass  # any key is a dep group
        elif "dependencies" not in key and "dependencies" not in current_section:
            continue

        # Find the content after the opening [
        after_bracket = stripped[array_match.end():]  # everything after '['
        # Strip inline comment from the remainder
        after_bracket = _strip_inline_comment(after_bracket)

        # Check for inline (single-line) array: has ] on the same line
        close_pos = after_bracket.rfind("]")
        if close_pos >= 0:
            # Entire array is on one line
            items_text = after_bracket[:close_pos]
            deps: list[str] = []
            for item in _parse_array_items_from_line(items_text):
                val = _extract_string_value(item)
                if val:
                    deps.append(val)
            results.append((deps, key))
        else:
            # Multi-line array: start collecting
            in_dep_array = True
            current_deps = []
            current_label = key
            array_depth = 1  # we've seen the opening [
            # Parse any items already on the opening line
            for item in _parse_array_items_from_line(after_bracket):
                val = _extract_string_value(item)
                if val:
                    current_deps.append(val)

    # Handle unclosed array (shouldn't happen in valid TOML)
    if in_dep_array and current_deps:
        results.append((current_deps, current_label))

    return results


def _parse_array_items_from_line(text: str) -> list[str]:
    """
    Extract quoted string tokens from a TOML array line fragment.
    Returns a list of raw tokens (still quoted, may have trailing commas).

    Inline table objects ({...}) are skipped — they are not dependency
    specifier strings (e.g. PEP 735 include-group directives).
    """
    tokens: list[str] = []
    i = 0
    in_inline_table = 0  # brace nesting depth
    while i < len(text):
        ch = text[i]
        if ch == '{':
            in_inline_table += 1
            i += 1
        elif ch == '}':
            in_inline_table = max(0, in_inline_table - 1)
            i += 1
        elif in_inline_table:
            i += 1  # skip everything inside inline tables
        elif ch in ('"', "'"):
            quote = ch
            j = i + 1
            while j < len(text) and text[j] != quote:
                if text[j] == '\\':
                    j += 1  # skip escaped char
                j += 1
            tokens.append(text[i:j + 1])
            i = j + 1
        else:
            i += 1
    return tokens


def _is_poetry_dep_section(section: str) -> bool:
    """Return True if the section name is a Poetry dependency section."""
    if section in (_POETRY_DEPS_SECTION, _POETRY_DEV_DEPS_SECTION):
        return True
    return bool(_POETRY_GROUP_DEPS_RE.match(section))


# Matches a simple key = "value" TOML assignment (no array)
_TOML_KV_RE = re.compile(r'^\s*([A-Za-z0-9_.\-]+)\s*=\s*(.+)$')

# Matches an inline table: { version = "...", ... }
_INLINE_TABLE_VERSION_RE = re.compile(r'version\s*=\s*["\']([^"\']+)["\']')

# Operators that are NOT exact pins in Poetry
# Acceptable: ==x.y.z or a bare x.y.z (no operator)
_POETRY_BAD_CONSTRAINT_RE = re.compile(r'^(\^|~|>=|>(?!=)|<=|<(?!=)|!=|\*)')


def _check_poetry_version(pkg: str, version: str) -> tuple[bool, str]:
    """
    Check whether a Poetry version constraint is an exact pin.

    Returns (is_bad, reason_msg).
    Acceptable:  ==x.y.z  or  x.y.z  (bare version, no operator — exact in Poetry)
    Flagged:     ^x, ~x, >=x, >x, <x, <=x, !=x, *
    """
    v = version.strip()
    if not v:
        return True, "'{}' has no version constraint; use exact pinning".format(pkg)

    if v == "*":
        return True, "'{}' uses wildcard '*'; use exact pinning".format(pkg)

    if v.startswith("=="):
        # Must have something after ==
        remainder = v[2:].strip()
        if remainder:
            return False, ""
        return True, "'{}' has empty == constraint; use exact pinning".format(pkg)

    if _POETRY_BAD_CONSTRAINT_RE.match(v):
        return True, "'{}' uses '{}' instead of exact pinning; use ==<version> or a bare version".format(
            pkg, v
        )

    # Bare version (no operator) — treat as exact in Poetry
    return False, ""


def extract_poetry_dependencies(content: str) -> list[tuple[str, str, int]]:
    """
    Parse pyproject.toml content and extract Poetry-style dependency entries.

    Returns a list of (package_name, version_string, line_number) tuples.
    Only entries under Poetry dependency sections are returned:
      - [tool.poetry.dependencies]
      - [tool.poetry.dev-dependencies]
      - [tool.poetry.group.<name>.dependencies]

    The 'python' key is skipped (it is a Python version constraint, not a package dep).
    Line numbers are 1-based.
    """
    results: list[tuple[str, str, int]] = []
    current_section = ""
    lines = content.splitlines()

    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()

        # Section header
        section_match = _SECTION_RE.match(stripped)
        if section_match:
            current_section = section_match.group(1).strip()
            continue

        if not _is_poetry_dep_section(current_section):
            continue

        # Skip blank lines and comments
        if not stripped or stripped.startswith("#"):
            continue

        kv_match = _TOML_KV_RE.match(stripped)
        if not kv_match:
            continue

        pkg = kv_match.group(1).strip()
        raw_value = kv_match.group(2).strip()

        # Skip the 'python' key
        if pkg.lower() == "python":
            continue

        # Inline table: { version = "^2.3", ... }
        if raw_value.startswith("{"):
            it_match = _INLINE_TABLE_VERSION_RE.search(raw_value)
            if it_match:
                version = it_match.group(1)
                results.append((pkg, version, lineno))
            else:
                # No version key — git/url dep, not content-addressed
                results.append((pkg, "", lineno))
            continue

        # Simple string: "^2.28"
        # Strip surrounding quotes
        version = _extract_string_value(raw_value)
        results.append((pkg, version, lineno))

    return results


def _check_dep_specifier(dep: str) -> tuple[bool, str]:
    """
    Check if a dependency specifier is exactly == pinned.

    Returns (is_bad, reason_msg) where is_bad=True means it should be flagged.
    """
    dep = dep.strip()
    if not dep:
        return False, ""

    # PEP 508 direct references: "package @ URL"
    # Only accept if the URL pins to an immutable commit SHA.
    # Mutable refs (@main, @v1.0, branch names) are not content-addressed.
    if " @ " in dep:
        url_part = dep.split(" @ ", 1)[1].strip()
        # git URLs: accept if pinned to a 40-char commit SHA after the last @
        if "git+" in url_part or "git://" in url_part:
            at_pos = url_part.rfind("@")
            if at_pos >= 0:
                ref = url_part[at_pos + 1:]
                if re.match(r'^[0-9a-f]{40}$', ref):
                    return False, ""
            pkg_name = dep.split(" @ ", 1)[0].strip()
            return True, "'{}' uses a mutable git ref; pin to a full commit SHA".format(pkg_name)
        # Archive/HTTP URLs: validate hash fragments
        pkg_name = dep.split(" @ ", 1)[0].strip()
        hash_errors = validate_url_fragment_hashes(url_part)
        if not hash_errors:
            return False, ""
        return True, "'{}' uses a URL reference without valid hash verification ({})".format(
            pkg_name, "; ".join(hash_errors),
        )

    m = _DEP_RE.match(dep)
    if not m:
        # Can't parse: flag it
        return True, "cannot parse dependency '{}'".format(dep)

    pkg_name = m.group(1)
    operator = m.group(2) or ""
    version = m.group(3) or ""

    if operator == "==" and version:
        return False, ""  # clean

    if not operator and not version:
        return True, "'{}' is not pinned; use {}==<version>".format(pkg_name, pkg_name)

    return True, "'{}' uses '{}' instead of '=='; use exact pinning".format(dep.split(";")[0].strip(), operator)


# Lock files that provide hash verification for pyproject.toml dependencies.
# requirements*.txt is matched dynamically via fnmatch (covers requirements.txt,
# requirements-dev.txt, requirements-prod.txt, etc.).
_LOCK_FILES_EXACT = frozenset([
    "poetry.lock",
    "pdm.lock",
    "uv.lock",
])


def _is_requirements_lock(filename: str) -> bool:
    """Return True if filename is a requirements*.txt lock file."""
    return fnmatch.fnmatch(filename, "requirements*.txt")


def _normalize_python_name(name: str) -> str:
    """Normalize a Python package name per PEP 503: lowercase, replace - and . with _."""
    # Strip extras like [extra1,extra2]
    bracket = name.find("[")
    if bracket >= 0:
        name = name[:bracket]
    return re.sub(r'[-._]+', '_', name.strip().lower())


def _extract_name_from_dep(dep: str) -> str:
    """Extract the package name from a PEP 508 dependency string."""
    m = _DEP_RE.match(dep.strip())
    if m:
        return m.group(1)
    # Fallback: take everything before first operator/space/semicolon
    for ch in ('>', '<', '=', '!', '~', ';', ' ', '['):
        pos = dep.find(ch)
        if pos >= 0:
            return dep[:pos]
    return dep


def _extract_lock_file_names(full_path: str, lock_filename: str) -> set[str]:
    """Extract normalized package names from a Python lock file."""
    lock_path = os.path.join(os.path.dirname(full_path), lock_filename)
    names: set[str] = set()
    try:
        with open(lock_path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except OSError:
        return names

    if _is_requirements_lock(lock_filename):
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            # Extract package name (before ==, >=, [, space, etc.)
            m = _DEP_RE.match(line)
            if m:
                names.add(_normalize_python_name(m.group(1)))
    else:
        # poetry.lock, pdm.lock, uv.lock: look for name = "pkg" lines
        name_re = re.compile(r'^name\s*=\s*"([^"]+)"')
        for line in content.splitlines():
            m = name_re.match(line.strip())
            if m:
                names.add(_normalize_python_name(m.group(1)))

    return names


class PyprojectChecker(Checker):
    name = "pyproject"
    description = (
        "Checks pyproject.toml [project], [dependency-groups], and [tool.poetry] dependencies for == exact pinning"
    )
    patterns: list[str] = ["pyproject.toml", "requirements*.txt", "poetry.lock", "pdm.lock", "uv.lock"]

    def check(self, index: FileIndex, root: str) -> list[Finding]:
        findings: list[Finding] = []

        for dir_path in sorted(index.keys()):
            files = index[dir_path]
            if "pyproject.toml" not in files:
                continue

            full_path = os.path.join(dir_path, "pyproject.toml")
            rel_path = os.path.relpath(full_path, root)

            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                    content = fh.read()
                    raw_lines = content.splitlines()
            except OSError:
                continue

            dep_arrays = extract_dependency_arrays(content)
            poetry_deps = extract_poetry_dependencies(content)

            # Check each PEP 621 dep for == pinning
            has_deps = False
            for deps, label in dep_arrays:
                if deps:
                    has_deps = True
                for dep in deps:
                    is_bad, msg = _check_dep_specifier(dep)
                    if not is_bad:
                        continue

                    lineno = _find_dep_line(raw_lines, dep)

                    findings.append(Finding(
                        checker=self.name,
                        path=rel_path,
                        line=lineno,
                        message=msg,
                    ))

            # Check each Poetry dep for exact pinning
            for pkg, version, lineno in poetry_deps:
                if pkg or version:
                    has_deps = True
                is_bad, msg = _check_poetry_version(pkg, version)
                if is_bad:
                    findings.append(Finding(
                        checker=self.name,
                        path=rel_path,
                        line=lineno,
                        message=msg,
                    ))

            # Check for companion lock file with hash verification.
            # Collect all requirements*.txt files and exact-match lock files.
            req_files = sorted(f for f in files if _is_requirements_lock(f))
            exact_lock_files = sorted(files & _LOCK_FILES_EXACT)
            all_lock_files = req_files + exact_lock_files

            if has_deps and not all_lock_files:
                findings.append(Finding(
                    checker=self.name,
                    path=rel_path,
                    line=0,
                    message="pyproject.toml has dependencies but no lock file with hash verification (requirements.txt, poetry.lock, pdm.lock, or uv.lock)",
                    integrity=True,
                ))
            elif has_deps and all_lock_files:
                # Cross-reference: check that every manifest dep appears in
                # at least one lock file.  Merge names from all lock files
                # (supports split lockfiles like requirements.txt + requirements-dev.txt).
                lock_names: set[str] = set()
                for lock_filename in all_lock_files:
                    lock_names |= _extract_lock_file_names(full_path, lock_filename)
                if lock_names:
                    missing: list[str] = []
                    for deps, label in dep_arrays:
                        for dep in deps:
                            raw_name = _extract_name_from_dep(dep)
                            normalized = _normalize_python_name(raw_name)
                            if normalized not in lock_names:
                                missing.append(normalized)
                    for pkg, version, lineno in poetry_deps:
                        normalized = _normalize_python_name(pkg)
                        if normalized not in lock_names:
                            missing.append(normalized)
                    if missing:
                        findings.append(Finding(
                            checker=self.name,
                            path=rel_path,
                            line=0,
                            message="lock files are stale: missing {} ({})".format(
                                "{} dependency".format(len(missing)) if len(missing) == 1 else "{} dependencies".format(len(missing)),
                                ", ".join(sorted(missing)),
                            ),
                            integrity=True,
                        ))

        return findings


def _find_dep_line(raw_lines: list[str], dep: str) -> int:
    """
    Search raw_lines for the line containing this dependency string.
    Returns 1-based line number, or 0 if not found.
    """
    # Escape for searching: just look for the dep string inside quotes
    pattern = re.compile(re.escape(dep))
    for i, line in enumerate(raw_lines):
        if pattern.search(line):
            return i + 1
    return 0
