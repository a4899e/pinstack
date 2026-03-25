"""Pyproject checker: enforces == pinning in [project] dependencies and optional-dependencies."""

from __future__ import annotations

import os
import re

from pinstack.core import Checker, Finding, FileIndex

# Sections we care about
_SECTION_PROJECT = "project"
_SECTION_OPTIONAL = "project.optional-dependencies"

# Matches a TOML section header like [project] or [project.optional-dependencies]
_SECTION_RE = re.compile(r'^\s*\[([^\]]+)\]')

# Matches a key = [ start of an array assignment
_ARRAY_START_RE = re.compile(r'^\s*(\w+)\s*=\s*\[')

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


def extract_dependency_arrays(content: str) -> list[tuple[list[str], str]]:
    """
    Parse pyproject.toml content with a state machine and extract dependency arrays.

    Returns a list of (deps, label) tuples where:
      deps  -- list of dependency specifier strings (quotes stripped)
      label -- human-readable label for the array (e.g. "dependencies", "dev")

    Only arrays under [project] (key == "dependencies") and
    [project.optional-dependencies] (any key) are returned.
    Other arrays like classifiers, requires, etc. are ignored.
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

        # --- Look for array start in valid sections ---
        if current_section not in (_SECTION_PROJECT, _SECTION_OPTIONAL):
            continue

        array_match = _ARRAY_START_RE.match(stripped)
        if not array_match:
            continue

        key = array_match.group(1)

        # Determine if this is a dependency array we care about
        if current_section == _SECTION_PROJECT and key != "dependencies":
            continue  # skip classifiers, requires, etc.
        # For optional-dependencies, any key is a dep group

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
    """
    tokens: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in ('"', "'"):
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


def _check_dep_specifier(dep: str) -> tuple[bool, str]:
    """
    Check if a dependency specifier is exactly == pinned.

    Returns (is_bad, reason_msg) where is_bad=True means it should be flagged.
    """
    dep = dep.strip()
    if not dep:
        return False, ""

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


# Lock files that provide hash verification for pyproject.toml dependencies
_LOCK_FILES = frozenset([
    "requirements.txt",  # pip-compile output with --generate-hashes
    "poetry.lock",
    "pdm.lock",
    "uv.lock",
])


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

    if lock_filename == "requirements.txt":
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
    description = "Checks pyproject.toml [project] dependencies for == exact pinning"
    patterns: list[str] = ["pyproject.toml", "requirements.txt", "poetry.lock", "pdm.lock", "uv.lock"]

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

            # Check each dep for == pinning
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

            # Check for companion lock file with hash verification
            found_lock_files = files & _LOCK_FILES
            if has_deps and not found_lock_files:
                findings.append(Finding(
                    checker=self.name,
                    path=rel_path,
                    line=0,
                    message="pyproject.toml has dependencies but no lock file with hash verification (requirements.txt, poetry.lock, pdm.lock, or uv.lock)",
                    integrity=True,
                ))
            elif has_deps and found_lock_files:
                # Cross-reference: check that every manifest dep appears in the lock file
                lock_filename = sorted(found_lock_files)[0]
                lock_names = _extract_lock_file_names(full_path, lock_filename)
                if lock_names:
                    missing: list[str] = []
                    for deps, label in dep_arrays:
                        for dep in deps:
                            raw_name = _extract_name_from_dep(dep)
                            normalized = _normalize_python_name(raw_name)
                            if normalized not in lock_names:
                                missing.append(normalized)
                    if missing:
                        findings.append(Finding(
                            checker=self.name,
                            path=rel_path,
                            line=0,
                            message="{} is stale: missing {} ({})".format(
                                lock_filename,
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
