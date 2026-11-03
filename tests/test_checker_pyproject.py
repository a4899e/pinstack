"""Tests for the pyproject checker."""

import os
import re
import tempfile
import shutil


from pinstack.checkers.pyproject import PyprojectChecker, extract_dependency_arrays

# Base directory for pyproject fixtures — each case lives in a named subdir
# containing a pyproject.toml file so the checker's pattern filter passes.
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "pyproject")


_NAME_TOKEN_RE = re.compile(
    r"[\"']\s*([A-Za-z0-9][A-Za-z0-9._-]*)|^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*=", re.M
)


def _write_covering_lock(dirpath):
    # type: (str) -> None
    """Write a poetry.lock naming every package-like token in pyproject.toml.

    Tests focused on pinning use this so they don't also pick up the
    companion-lock-file or staleness findings. Extra names are harmless. An
    existing poetry.lock is left alone so cross-reference tests keep theirs.
    """
    lock_path = os.path.join(dirpath, "poetry.lock")
    if os.path.exists(lock_path):
        return
    with open(os.path.join(dirpath, "pyproject.toml")) as fh:
        content = fh.read()
    names = set()
    for quoted, key in _NAME_TOKEN_RE.findall(content):
        names.add(quoted or key)
    with open(lock_path, "w") as fh:
        for name in sorted(names):
            fh.write('[[package]]\nname = "{}"\nversion = "1.0.0"\n\n'.format(name))


def _check_subdir(subdir_name):
    # type: (str) -> list
    """Run PyprojectChecker against a copy of a named fixture subdir.

    The copy gets a covering poetry.lock so tests focused on pinning checks
    don't also pick up the companion-lock-file warning.
    """
    checker = PyprojectChecker()
    tmpdir = tempfile.mkdtemp()
    try:
        shutil.copy(os.path.join(FIXTURES, subdir_name, "pyproject.toml"), tmpdir)
        _write_covering_lock(tmpdir)
        index = {tmpdir: {"pyproject.toml", "poetry.lock"}}
        return checker.check(index, tmpdir)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Unit tests for extract_dependency_arrays (state-machine parser)
# ---------------------------------------------------------------------------


class TestExtractDependencyArrays:
    def test_single_line_array(self):
        content = '[project]\ndependencies = ["requests==2.31.0", "flask==2.3.2"]\n'
        arrays = extract_dependency_arrays(content)
        assert len(arrays) == 1
        deps, label = arrays[0]
        assert "requests==2.31.0" in deps
        assert "flask==2.3.2" in deps

    def test_multi_line_array(self):
        content = (
            "[project]\n"
            "dependencies = [\n"
            '    "flask==2.3.2",\n'
            '    "requests==2.31.0",\n'
            "]\n"
        )
        arrays = extract_dependency_arrays(content)
        assert len(arrays) == 1
        deps, label = arrays[0]
        assert "flask==2.3.2" in deps
        assert "requests==2.31.0" in deps

    def test_inline_comments_stripped(self):
        content = (
            "[project]\n"
            "dependencies = [\n"
            '    "flask==2.3.2",  # web framework\n'
            '    "requests==2.31.0",  # http\n'
            "]\n"
        )
        arrays = extract_dependency_arrays(content)
        assert len(arrays) == 1
        deps, _ = arrays[0]
        assert "flask==2.3.2" in deps
        assert "requests==2.31.0" in deps

    def test_optional_dependencies_sections(self):
        content = (
            "[project.optional-dependencies]\n"
            "dev = [\n"
            '    "pytest==7.4.0",\n'
            "]\n"
            "docs = [\n"
            '    "sphinx==7.1.0",\n'
            "]\n"
        )
        arrays = extract_dependency_arrays(content)
        assert len(arrays) == 2
        all_deps = []
        for deps, label in arrays:
            all_deps.extend(deps)
        assert "pytest==7.4.0" in all_deps
        assert "sphinx==7.1.0" in all_deps

    def test_optional_dependencies_hyphenated_key(self):
        """Keys with hyphens like local-llm should be matched."""
        content = '[project.optional-dependencies]\nlocal-llm = ["mlx-lm>=0.20.0"]\n'
        arrays = extract_dependency_arrays(content)
        assert len(arrays) == 1
        deps, label = arrays[0]
        assert "mlx-lm>=0.20.0" in deps
        assert label == "local-llm"

    def test_quoted_hyphenated_key(self):
        """Quoted keys like "local-llm" should be matched."""
        content = '[project.optional-dependencies]\n"local-llm" = ["mlx-lm>=0.20.0"]\n'
        arrays = extract_dependency_arrays(content)
        assert len(arrays) == 1
        deps, label = arrays[0]
        assert "mlx-lm>=0.20.0" in deps
        assert label == "local-llm"

    def test_single_quoted_key(self):
        """Single-quoted keys should be matched."""
        content = "[dependency-groups]\n'dev-test' = [\"pytest>=8.3.0\"]\n"
        arrays = extract_dependency_arrays(content)
        assert len(arrays) == 1
        deps, label = arrays[0]
        assert "pytest>=8.3.0" in deps
        assert label == "dev-test"

    def test_dependency_groups_pep735(self):
        """[dependency-groups] (PEP 735) arrays should be extracted."""
        content = (
            "[dependency-groups]\n"
            "dev = [\n"
            '    "pytest==9.0.2",\n'
            '    "ruff==0.15.5",\n'
            "]\n"
        )
        arrays = extract_dependency_arrays(content)
        assert len(arrays) == 1
        deps, label = arrays[0]
        assert "pytest==9.0.2" in deps
        assert "ruff==0.15.5" in deps
        assert label == "dev"

    def test_dependency_groups_include_group_skipped(self):
        """PEP 735 {include-group = "base"} objects should not be parsed as deps."""
        content = (
            "[dependency-groups]\n"
            'base = ["pytest==8.3.0"]\n'
            'dev = [{include-group = "base"}, "ruff==0.15.5"]\n'
        )
        arrays = extract_dependency_arrays(content)
        assert len(arrays) == 2
        # base group
        base_deps = [d for deps, label in arrays if label == "base" for d in deps]
        assert "pytest==8.3.0" in base_deps
        # dev group should only have ruff, not "base"
        dev_deps = [d for deps, label in arrays if label == "dev" for d in deps]
        assert "ruff==0.15.5" in dev_deps
        assert "base" not in dev_deps

    def test_dependency_groups_include_group_multiline(self):
        """Multi-line arrays with include-group objects should skip the objects."""
        content = (
            "[dependency-groups]\n"
            'base = ["pytest==8.3.0"]\n'
            "dev = [\n"
            '    {include-group = "base"},\n'
            '    "ruff==0.15.5",\n'
            "]\n"
        )
        arrays = extract_dependency_arrays(content)
        dev_deps = [d for deps, label in arrays if label == "dev" for d in deps]
        assert "ruff==0.15.5" in dev_deps
        assert "base" not in dev_deps

    def test_hatch_env_dependencies(self):
        """[tool.hatch.envs.*.dependencies] arrays should be extracted."""
        content = (
            "[tool.hatch.envs.default]\n"
            "dependencies = [\n"
            '    "pytest==9.0.2",\n'
            '    "ruff>=0.15.0",\n'
            "]\n"
        )
        arrays = extract_dependency_arrays(content)
        assert len(arrays) == 1
        deps, label = arrays[0]
        assert "pytest==9.0.2" in deps
        assert "ruff>=0.15.0" in deps

    def test_pdm_dev_dependencies(self):
        """[tool.pdm.dev-dependencies] group arrays should be extracted."""
        content = (
            "[tool.pdm.dev-dependencies]\n"
            "test = [\n"
            '    "pytest>=8.3.0",\n'
            '    "ruff==0.5.0",\n'
            "]\n"
        )
        arrays = extract_dependency_arrays(content)
        assert len(arrays) == 1
        deps, label = arrays[0]
        assert "pytest>=8.3.0" in deps
        assert "ruff==0.5.0" in deps

    def test_tool_section_non_dep_key_ignored(self):
        """Arrays in tool sections whose key doesn't contain 'dependencies' are skipped."""
        content = '[tool.ruff.lint]\nselect = ["E", "F", "W"]\n'
        arrays = extract_dependency_arrays(content)
        assert len(arrays) == 0

    def test_ignores_classifiers_array(self):
        content = (
            "[project]\n"
            "classifiers = [\n"
            '    "Development Status :: 3 - Alpha",\n'
            "]\n"
            "dependencies = [\n"
            '    "flask==2.3.2",\n'
            "]\n"
        )
        arrays = extract_dependency_arrays(content)
        assert len(arrays) == 1
        deps, _ = arrays[0]
        assert "flask==2.3.2" in deps
        # classifier text should NOT be in deps
        assert not any("Development Status" in d for d in deps)

    def test_empty_array(self):
        content = "[project]\ndependencies = []\n"
        arrays = extract_dependency_arrays(content)
        # Either returns empty list or a single entry with empty deps
        total_deps = sum(len(deps) for deps, _ in arrays)
        assert total_deps == 0

    def test_no_section_header(self):
        """A bare 'dependencies' array before any section header is ignored."""
        content = 'dependencies = [\n    "flask==2.3.2",\n]\n'
        arrays = extract_dependency_arrays(content)
        assert len(arrays) == 0

    def test_trailing_comma_handled(self):
        content = (
            "[project]\n"
            "dependencies = [\n"
            '    "flask==2.3.2",\n'
            '    "requests==2.31.0",\n'  # trailing comma on last item
            "]\n"
        )
        arrays = extract_dependency_arrays(content)
        deps, _ = arrays[0]
        assert "flask==2.3.2" in deps
        assert "requests==2.31.0" in deps

    def test_single_line_inline_array_two_items(self):
        content = '[project]\ndependencies = ["requests==2.31.0", "flask>=2.0.0"]\n'
        arrays = extract_dependency_arrays(content)
        assert len(arrays) == 1
        deps, _ = arrays[0]
        assert len(deps) == 2

    def test_both_project_and_optional_deps(self):
        content = (
            "[project]\n"
            'dependencies = ["flask==2.3.2"]\n'
            "\n"
            "[project.optional-dependencies]\n"
            'dev = ["pytest==7.4.0"]\n'
        )
        arrays = extract_dependency_arrays(content)
        assert len(arrays) == 2
        all_deps = [d for deps, _ in arrays for d in deps]
        assert "flask==2.3.2" in all_deps
        assert "pytest==7.4.0" in all_deps

    def test_build_system_requires_ignored(self):
        """[build-system] requires array should NOT be checked as dependencies."""
        content = (
            "[build-system]\n"
            'requires = ["setuptools>=61.0", "wheel"]\n'
            'build-backend = "setuptools.build_meta"\n'
            "\n"
            "[project]\n"
            'dependencies = ["flask==2.3.2"]\n'
        )
        arrays = extract_dependency_arrays(content)
        # Only [project] dependencies should be returned
        assert len(arrays) == 1
        deps, _ = arrays[0]
        assert "flask==2.3.2" in deps
        # build-system requires should not appear
        assert not any("setuptools" in d for d in deps)


# ---------------------------------------------------------------------------
# Integration tests using fixture subdirectories
# ---------------------------------------------------------------------------


class TestPyprojectCheckerGood:
    def test_good_toml_no_findings(self):
        findings = _check_subdir("good")
        assert findings == [], "All ==pins should produce 0 findings, got: {}".format(
            findings
        )

    def test_checker_name(self):
        checker = PyprojectChecker()
        assert checker.name == "pyproject"

    def test_checker_patterns(self):
        checker = PyprojectChecker()
        assert "pyproject.toml" in checker.patterns


class TestPyprojectCheckerBad:
    def test_bad_toml_has_findings(self):
        findings = _check_subdir("bad")
        assert len(findings) == 3, (
            "flask>=, requests~=, certifi (bare) should give 3 findings"
        )

    def test_bad_toml_has_three_findings(self):
        findings = _check_subdir("bad")
        assert len(findings) == 3

    def test_bad_toml_ge_operator_flagged(self):
        findings = _check_subdir("bad")
        msgs = [f.message for f in findings]
        assert any("flask" in m for m in msgs), "flask>=2.0.0 should be flagged"

    def test_bad_toml_tilde_operator_flagged(self):
        findings = _check_subdir("bad")
        msgs = [f.message for f in findings]
        assert any("requests" in m for m in msgs), "requests~=2.31.0 should be flagged"

    def test_bad_toml_bare_name_flagged(self):
        findings = _check_subdir("bad")
        msgs = [f.message for f in findings]
        assert any("certifi" in m for m in msgs), "bare 'certifi' should be flagged"

    def test_bad_toml_classifiers_not_flagged(self):
        """classifiers = [...] entries must never appear as findings."""
        findings = _check_subdir("bad")
        msgs = [f.message for f in findings]
        assert not any("Development Status" in m for m in msgs)

    def test_findings_have_line_numbers(self):
        findings = _check_subdir("bad")
        for f in findings:
            assert f.line > 0

    def test_findings_checker_name(self):
        findings = _check_subdir("bad")
        for f in findings:
            assert f.checker == "pyproject"


class TestPyprojectCheckerInline:
    def test_inline_single_line_array(self):
        """Single-line array: only flask>=2.0.0 should be flagged."""
        findings = _check_subdir("inline")
        assert len(findings) == 1
        assert "flask" in findings[0].message

    def test_inline_has_one_finding(self):
        findings = _check_subdir("inline")
        assert len(findings) == 1


class TestPyprojectCheckerNoDeps:
    def test_no_deps_no_findings(self):
        findings = _check_subdir("no_deps")
        assert findings == []


class TestPyprojectCheckerNoFile:
    def test_no_pyproject_file_no_findings(self):
        checker = PyprojectChecker()
        # Empty index -> no files -> no findings
        findings = checker.check({}, "/tmp")
        assert findings == []

    def test_dir_without_pyproject_no_findings(self):
        tmpdir = tempfile.mkdtemp()
        try:
            checker = PyprojectChecker()
            # Index has directory but no pyproject.toml
            index = {tmpdir: {"setup.py", "requirements.txt"}}
            findings = checker.check(index, tmpdir)
            assert findings == []
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestPyprojectCheckerPaths:
    def test_finding_path_is_relative(self):
        findings = _check_subdir("bad")
        for f in findings:
            assert not os.path.isabs(f.path)

    def test_finding_path_contains_filename(self):
        findings = _check_subdir("bad")
        for f in findings:
            assert "pyproject.toml" in f.path


class TestPyprojectCheckerOptionalDeps:
    def test_optional_deps_checked(self):
        """Optional-dependency entries with >= should produce findings."""
        tmpdir = tempfile.mkdtemp()
        try:
            toml_path = os.path.join(tmpdir, "pyproject.toml")
            with open(toml_path, "w") as fh:
                fh.write(
                    "[project.optional-dependencies]\n"
                    "dev = [\n"
                    '    "pytest>=7.0",\n'
                    '    "black==23.7.0",\n'
                    "]\n"
                )
            checker = PyprojectChecker()
            _write_covering_lock(tmpdir)
            index = {tmpdir: {"pyproject.toml", "poetry.lock"}}
            findings = checker.check(index, tmpdir)
            assert len(findings) == 1
            assert "pytest" in findings[0].message
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestPyprojectCheckerHyphenatedOptionalKey:
    def test_hyphenated_optional_dep_key_flagged(self):
        """Hyphenated keys like local-llm under optional-dependencies should be checked."""
        tmpdir = tempfile.mkdtemp()
        try:
            toml_path = os.path.join(tmpdir, "pyproject.toml")
            with open(toml_path, "w") as fh:
                fh.write(
                    '[project.optional-dependencies]\nlocal-llm = ["mlx-lm>=0.20.0"]\n'
                )
            checker = PyprojectChecker()
            _write_covering_lock(tmpdir)
            index = {tmpdir: {"pyproject.toml", "poetry.lock"}}
            findings = checker.check(index, tmpdir)
            pin_findings = [
                f
                for f in findings
                if "lock file" not in f.message and "stale" not in f.message
            ]
            assert len(pin_findings) == 1
            assert "mlx-lm" in pin_findings[0].message
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestDependencyGroups:
    def test_dependency_groups_unpinned_flagged(self):
        """[dependency-groups] (PEP 735) entries with >= should be flagged."""
        tmpdir = tempfile.mkdtemp()
        try:
            toml_path = os.path.join(tmpdir, "pyproject.toml")
            with open(toml_path, "w") as fh:
                fh.write(
                    "[dependency-groups]\n"
                    "dev = [\n"
                    '    "pytest>=9.0",\n'
                    '    "ruff==0.15.5",\n'
                    "]\n"
                )
            checker = PyprojectChecker()
            _write_covering_lock(tmpdir)
            index = {tmpdir: {"pyproject.toml", "poetry.lock"}}
            findings = checker.check(index, tmpdir)
            pin_findings = [
                f
                for f in findings
                if "lock file" not in f.message and "stale" not in f.message
            ]
            assert len(pin_findings) == 1
            assert "pytest" in pin_findings[0].message
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_dependency_groups_pinned_clean(self):
        """[dependency-groups] with all == pins should produce 0 pin findings."""
        tmpdir = tempfile.mkdtemp()
        try:
            toml_path = os.path.join(tmpdir, "pyproject.toml")
            with open(toml_path, "w") as fh:
                fh.write(
                    "[dependency-groups]\n"
                    "dev = [\n"
                    '    "pytest==9.0.2",\n'
                    '    "ruff==0.15.5",\n'
                    "]\n"
                )
            checker = PyprojectChecker()
            _write_covering_lock(tmpdir)
            index = {tmpdir: {"pyproject.toml", "poetry.lock"}}
            findings = checker.check(index, tmpdir)
            pin_findings = [
                f
                for f in findings
                if "lock file" not in f.message and "stale" not in f.message
            ]
            assert pin_findings == []
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_include_group_not_flagged_as_dep(self):
        """PEP 735 {include-group = "base"} should not produce a bogus finding."""
        tmpdir = tempfile.mkdtemp()
        try:
            toml_path = os.path.join(tmpdir, "pyproject.toml")
            with open(toml_path, "w") as fh:
                fh.write(
                    "[dependency-groups]\n"
                    'base = ["pytest==8.3.0"]\n'
                    'dev = [{include-group = "base"}, "ruff==0.15.5"]\n'
                )
            checker = PyprojectChecker()
            _write_covering_lock(tmpdir)
            index = {tmpdir: {"pyproject.toml", "poetry.lock"}}
            findings = checker.check(index, tmpdir)
            pin_findings = [
                f
                for f in findings
                if "lock file" not in f.message and "stale" not in f.message
            ]
            assert pin_findings == [], (
                "include-group objects should not produce findings, got: {}".format(
                    [f.message for f in pin_findings]
                )
            )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Lock file companion check tests
# ---------------------------------------------------------------------------

_PYPROJECT_WITH_DEPS = (
    "[project]\n"
    'name = "myapp"\n'
    'version = "1.0.0"\n'
    "dependencies = [\n"
    '    "flask==2.3.2",\n'
    "]\n"
)

_PYPROJECT_NO_DEPS = '[project]\nname = "myapp"\nversion = "1.0.0"\n'


def _make_pyproject(tmpdir, content):
    # type: (str, str) -> None
    path = os.path.join(tmpdir, "pyproject.toml")
    with open(path, "w") as fh:
        fh.write(content)


class TestPyprojectLockFileCheck:
    def test_no_lock_file_warns(self):
        tmpdir = tempfile.mkdtemp()
        try:
            _make_pyproject(tmpdir, _PYPROJECT_WITH_DEPS)
            checker = PyprojectChecker()
            index = {tmpdir: {"pyproject.toml"}}
            findings = checker.check(index, tmpdir)
            lock_warnings = [f for f in findings if "lock file" in f.message]
            assert len(lock_warnings) == 1
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_requirements_txt_satisfies(self):
        tmpdir = tempfile.mkdtemp()
        try:
            _make_pyproject(tmpdir, _PYPROJECT_WITH_DEPS)
            with open(os.path.join(tmpdir, "requirements.txt"), "w") as fh:
                fh.write("flask==2.3.2\n")
            checker = PyprojectChecker()
            index = {tmpdir: {"pyproject.toml", "requirements.txt"}}
            findings = checker.check(index, tmpdir)
            lock_warnings = [f for f in findings if "lock file" in f.message]
            assert lock_warnings == []
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_poetry_lock_satisfies(self):
        tmpdir = tempfile.mkdtemp()
        try:
            _make_pyproject(tmpdir, _PYPROJECT_WITH_DEPS)
            checker = PyprojectChecker()
            _write_covering_lock(tmpdir)
            index = {tmpdir: {"pyproject.toml", "poetry.lock"}}
            findings = checker.check(index, tmpdir)
            lock_warnings = [f for f in findings if "lock file" in f.message]
            assert lock_warnings == []
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_pdm_lock_satisfies(self):
        tmpdir = tempfile.mkdtemp()
        try:
            _make_pyproject(tmpdir, _PYPROJECT_WITH_DEPS)
            with open(os.path.join(tmpdir, "pdm.lock"), "w") as fh:
                fh.write('[[package]]\nname = "flask"\nversion = "2.3.2"\n')
            checker = PyprojectChecker()
            index = {tmpdir: {"pyproject.toml", "pdm.lock"}}
            findings = checker.check(index, tmpdir)
            assert findings == []
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_uv_lock_satisfies(self):
        tmpdir = tempfile.mkdtemp()
        try:
            _make_pyproject(tmpdir, _PYPROJECT_WITH_DEPS)
            with open(os.path.join(tmpdir, "uv.lock"), "w") as fh:
                fh.write('[[package]]\nname = "flask"\nversion = "2.3.2"\n')
            checker = PyprojectChecker()
            index = {tmpdir: {"pyproject.toml", "uv.lock"}}
            findings = checker.check(index, tmpdir)
            assert findings == []
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_no_deps_no_warning(self):
        tmpdir = tempfile.mkdtemp()
        try:
            _make_pyproject(tmpdir, _PYPROJECT_NO_DEPS)
            checker = PyprojectChecker()
            index = {tmpdir: {"pyproject.toml"}}
            findings = checker.check(index, tmpdir)
            assert findings == []
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_lock_file_finding_present(self):
        tmpdir = tempfile.mkdtemp()
        try:
            _make_pyproject(tmpdir, _PYPROJECT_WITH_DEPS)
            checker = PyprojectChecker()
            index = {tmpdir: {"pyproject.toml"}}
            findings = checker.check(index, tmpdir)
            lock_findings = [f for f in findings if "lock file" in f.message]
            assert len(lock_findings) == 1
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Lock file cross-reference tests
# ---------------------------------------------------------------------------

_PYPROJECT_TWO_DEPS = (
    "[project]\n"
    'name = "myapp"\n'
    'version = "1.0.0"\n'
    "dependencies = [\n"
    '    "flask==2.0",\n'
    '    "requests==2.28.0",\n'
    "]\n"
)


class TestPyprojectLockFileCrossRef:
    def test_dep_missing_from_requirements_txt(self):
        tmpdir = tempfile.mkdtemp()
        try:
            _make_pyproject(tmpdir, _PYPROJECT_TWO_DEPS)
            req_path = os.path.join(tmpdir, "requirements.txt")
            with open(req_path, "w") as fh:
                fh.write("requests==2.28.0\n")
            checker = PyprojectChecker()
            index = {tmpdir: {"pyproject.toml", "requirements.txt"}}
            findings = checker.check(index, tmpdir)
            cross_ref = [f for f in findings if "stale" in f.message]
            assert len(cross_ref) == 1
            assert "flask" in cross_ref[0].message
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_split_lockfiles_merged(self):
        """Deps split across requirements.txt and requirements-dev.txt should all count."""
        tmpdir = tempfile.mkdtemp()
        try:
            _make_pyproject(tmpdir, _PYPROJECT_TWO_DEPS)
            with open(os.path.join(tmpdir, "requirements.txt"), "w") as fh:
                fh.write("flask==2.0\n")
            with open(os.path.join(tmpdir, "requirements-dev.txt"), "w") as fh:
                fh.write("requests==2.28.0\n")
            checker = PyprojectChecker()
            index = {
                tmpdir: {"pyproject.toml", "requirements.txt", "requirements-dev.txt"}
            }
            findings = checker.check(index, tmpdir)
            cross_ref = [f for f in findings if "stale" in f.message]
            assert cross_ref == [], (
                "split lockfiles should satisfy cross-ref, got: {}".format(
                    [f.message for f in cross_ref]
                )
            )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_requirements_dev_satisfies(self):
        """requirements-dev.txt alone should satisfy the lock file check."""
        tmpdir = tempfile.mkdtemp()
        try:
            _make_pyproject(tmpdir, _PYPROJECT_TWO_DEPS)
            with open(os.path.join(tmpdir, "requirements-dev.txt"), "w") as fh:
                fh.write("flask==2.0\nrequests==2.28.0\n")
            checker = PyprojectChecker()
            index = {tmpdir: {"pyproject.toml", "requirements-dev.txt"}}
            findings = checker.check(index, tmpdir)
            lock_warnings = [f for f in findings if "lock file" in f.message]
            assert lock_warnings == []
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_comment_only_requirements_not_a_lock_file(self):
        """A comment-only requirements-dev.txt should not suppress the lock file warning."""
        tmpdir = tempfile.mkdtemp()
        try:
            _make_pyproject(tmpdir, _PYPROJECT_WITH_DEPS)
            with open(os.path.join(tmpdir, "requirements-dev.txt"), "w") as fh:
                fh.write("# this file only has comments\n# no packages\n")
            checker = PyprojectChecker()
            index = {tmpdir: {"pyproject.toml", "requirements-dev.txt"}}
            findings = checker.check(index, tmpdir)
            lock_warnings = [f for f in findings if "lock file" in f.message]
            assert len(lock_warnings) == 1
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_dep_present_in_requirements_txt(self):
        tmpdir = tempfile.mkdtemp()
        try:
            _make_pyproject(tmpdir, _PYPROJECT_TWO_DEPS)
            req_path = os.path.join(tmpdir, "requirements.txt")
            with open(req_path, "w") as fh:
                fh.write("flask==2.0\nrequests==2.28.0\n")
            checker = PyprojectChecker()
            index = {tmpdir: {"pyproject.toml", "requirements.txt"}}
            findings = checker.check(index, tmpdir)
            cross_ref = [f for f in findings if "stale" in f.message]
            assert cross_ref == []
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_dep_name_normalization(self):
        """Capital letters in pyproject.toml should match lowercase in lock file."""
        tmpdir = tempfile.mkdtemp()
        try:
            toml_content = (
                '[project]\nname = "myapp"\ndependencies = [\n    "Flask==2.0",\n]\n'
            )
            _make_pyproject(tmpdir, toml_content)
            req_path = os.path.join(tmpdir, "requirements.txt")
            with open(req_path, "w") as fh:
                fh.write("flask==2.0\n")
            checker = PyprojectChecker()
            index = {tmpdir: {"pyproject.toml", "requirements.txt"}}
            findings = checker.check(index, tmpdir)
            cross_ref = [f for f in findings if "stale" in f.message]
            assert cross_ref == []
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_dep_name_dash_underscore(self):
        """Dashes and underscores should be treated as equivalent (PEP 503)."""
        tmpdir = tempfile.mkdtemp()
        try:
            toml_content = (
                "[project]\n"
                'name = "myapp"\n'
                "dependencies = [\n"
                '    "my-package==1.0",\n'
                "]\n"
            )
            _make_pyproject(tmpdir, toml_content)
            req_path = os.path.join(tmpdir, "requirements.txt")
            with open(req_path, "w") as fh:
                fh.write("my_package==1.0\n")
            checker = PyprojectChecker()
            index = {tmpdir: {"pyproject.toml", "requirements.txt"}}
            findings = checker.check(index, tmpdir)
            cross_ref = [f for f in findings if "stale" in f.message]
            assert cross_ref == []
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_poetry_lock_cross_ref(self):
        tmpdir = tempfile.mkdtemp()
        try:
            _make_pyproject(tmpdir, _PYPROJECT_TWO_DEPS)
            lock_path = os.path.join(tmpdir, "poetry.lock")
            with open(lock_path, "w") as fh:
                fh.write(
                    "[[package]]\n"
                    'name = "requests"\n'
                    'version = "2.28.0"\n'
                    "\n"
                    "[[package]]\n"
                    'name = "flask"\n'
                    'version = "2.0"\n'
                )
            checker = PyprojectChecker()
            _write_covering_lock(tmpdir)
            index = {tmpdir: {"pyproject.toml", "poetry.lock"}}
            findings = checker.check(index, tmpdir)
            cross_ref = [f for f in findings if "stale" in f.message]
            assert cross_ref == []
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_uv_lock_cross_ref(self):
        tmpdir = tempfile.mkdtemp()
        try:
            _make_pyproject(tmpdir, _PYPROJECT_TWO_DEPS)
            lock_path = os.path.join(tmpdir, "uv.lock")
            with open(lock_path, "w") as fh:
                fh.write(
                    "[[package]]\n"
                    'name = "requests"\n'
                    'version = "2.28.0"\n'
                    "\n"
                    "[[package]]\n"
                    'name = "flask"\n'
                    'version = "2.0"\n'
                )
            checker = PyprojectChecker()
            index = {tmpdir: {"pyproject.toml", "uv.lock"}}
            findings = checker.check(index, tmpdir)
            cross_ref = [f for f in findings if "stale" in f.message]
            assert cross_ref == []
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Poetry dependency tests
# ---------------------------------------------------------------------------


def _make_poetry_pyproject(tmpdir, content):
    # type: (str, str) -> None
    """Write pyproject.toml and a poetry.lock stub so the lock-file warning is suppressed."""
    toml_path = os.path.join(tmpdir, "pyproject.toml")
    lock_path = os.path.join(tmpdir, "poetry.lock")
    with open(toml_path, "w") as fh:
        fh.write(content)
    # Minimal poetry.lock containing all package names referenced in content,
    # so cross-reference checks don't fire. Tests that want to test cross-ref
    # behaviour create their own lock files.
    with open(lock_path, "w") as fh:
        fh.write(
            '[[package]]\nname = "requests"\nversion = "2.28.0"\n\n'
            '[[package]]\nname = "flask"\nversion = "2.3.0"\n\n'
            '[[package]]\nname = "boto3"\nversion = "1.35.0"\n\n'
            '[[package]]\nname = "pytest"\nversion = "7.4.0"\n\n'
            '[[package]]\nname = "coverage"\nversion = "7.3.0"\n\n'
            '[[package]]\nname = "black"\nversion = "23.7.0"\n\n'
        )


class TestPoetryDependencies:
    def test_poetry_deps_good(self):
        """All == pinned Poetry deps produce 0 findings (excluding lock file finding)."""
        tmpdir = tempfile.mkdtemp()
        try:
            _make_poetry_pyproject(
                tmpdir,
                (
                    "[tool.poetry.dependencies]\n"
                    'python = "^3.9"\n'
                    'requests = "==2.28.0"\n'
                    'flask = "==2.3.0"\n'
                ),
            )
            checker = PyprojectChecker()
            _write_covering_lock(tmpdir)
            index = {tmpdir: {"pyproject.toml", "poetry.lock"}}
            findings = checker.check(index, tmpdir)
            pin_findings = [
                f
                for f in findings
                if "lock file" not in f.message and "stale" not in f.message
            ]
            assert pin_findings == [], (
                "All == pins should produce 0 findings, got: {}".format(pin_findings)
            )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_poetry_deps_caret(self):
        """Caret constraint ^2.28 should be flagged."""
        tmpdir = tempfile.mkdtemp()
        try:
            _make_poetry_pyproject(
                tmpdir,
                ('[tool.poetry.dependencies]\npython = "^3.9"\nrequests = "^2.28"\n'),
            )
            checker = PyprojectChecker()
            _write_covering_lock(tmpdir)
            index = {tmpdir: {"pyproject.toml", "poetry.lock"}}
            findings = checker.check(index, tmpdir)
            pin_findings = [
                f
                for f in findings
                if "lock file" not in f.message and "stale" not in f.message
            ]
            assert len(pin_findings) == 1
            assert "requests" in pin_findings[0].message

        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_poetry_deps_tilde(self):
        """Tilde constraint ~2.28 should be flagged."""
        tmpdir = tempfile.mkdtemp()
        try:
            _make_poetry_pyproject(
                tmpdir,
                ('[tool.poetry.dependencies]\npython = "^3.9"\nflask = "~2.3"\n'),
            )
            checker = PyprojectChecker()
            _write_covering_lock(tmpdir)
            index = {tmpdir: {"pyproject.toml", "poetry.lock"}}
            findings = checker.check(index, tmpdir)
            pin_findings = [
                f
                for f in findings
                if "lock file" not in f.message and "stale" not in f.message
            ]
            assert len(pin_findings) == 1
            assert "flask" in pin_findings[0].message
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_poetry_deps_inline_table(self):
        """{version = "^2.3"} inline table constraint should be flagged."""
        tmpdir = tempfile.mkdtemp()
        try:
            _make_poetry_pyproject(
                tmpdir,
                (
                    "[tool.poetry.dependencies]\n"
                    'python = "^3.9"\n'
                    'flask = {version = "^2.3", extras = ["async"]}\n'
                ),
            )
            checker = PyprojectChecker()
            _write_covering_lock(tmpdir)
            index = {tmpdir: {"pyproject.toml", "poetry.lock"}}
            findings = checker.check(index, tmpdir)
            pin_findings = [
                f
                for f in findings
                if "lock file" not in f.message and "stale" not in f.message
            ]
            assert len(pin_findings) == 1
            assert "flask" in pin_findings[0].message
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_poetry_deps_python_skipped(self):
        """python = "^3.9" should NOT be flagged (python version constraint)."""
        tmpdir = tempfile.mkdtemp()
        try:
            _make_poetry_pyproject(
                tmpdir,
                (
                    "[tool.poetry.dependencies]\n"
                    'python = "^3.9"\n'
                    'requests = "==2.28.0"\n'
                ),
            )
            checker = PyprojectChecker()
            _write_covering_lock(tmpdir)
            index = {tmpdir: {"pyproject.toml", "poetry.lock"}}
            findings = checker.check(index, tmpdir)
            pin_findings = [
                f
                for f in findings
                if "lock file" not in f.message and "stale" not in f.message
            ]
            python_findings = [f for f in pin_findings if "python" in f.message.lower()]
            assert python_findings == [], "python key should never be flagged"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_poetry_dev_deps_checked(self):
        """[tool.poetry.dev-dependencies] should be scanned."""
        tmpdir = tempfile.mkdtemp()
        try:
            _make_poetry_pyproject(
                tmpdir,
                (
                    "[tool.poetry.dependencies]\n"
                    'python = "^3.9"\n'
                    "\n"
                    "[tool.poetry.dev-dependencies]\n"
                    'pytest = "^7.0"\n'
                ),
            )
            checker = PyprojectChecker()
            _write_covering_lock(tmpdir)
            index = {tmpdir: {"pyproject.toml", "poetry.lock"}}
            findings = checker.check(index, tmpdir)
            pin_findings = [
                f
                for f in findings
                if "lock file" not in f.message and "stale" not in f.message
            ]
            assert len(pin_findings) == 1
            assert "pytest" in pin_findings[0].message
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_poetry_group_deps_checked(self):
        """[tool.poetry.group.test.dependencies] should be scanned."""
        tmpdir = tempfile.mkdtemp()
        try:
            _make_poetry_pyproject(
                tmpdir,
                (
                    "[tool.poetry.dependencies]\n"
                    'python = "^3.9"\n'
                    "\n"
                    "[tool.poetry.group.test.dependencies]\n"
                    'coverage = "^7.0"\n'
                ),
            )
            checker = PyprojectChecker()
            _write_covering_lock(tmpdir)
            index = {tmpdir: {"pyproject.toml", "poetry.lock"}}
            findings = checker.check(index, tmpdir)
            pin_findings = [
                f
                for f in findings
                if "lock file" not in f.message and "stale" not in f.message
            ]
            assert len(pin_findings) == 1
            assert "coverage" in pin_findings[0].message
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_poetry_wildcard_flagged(self):
        """Wildcard * constraint should be flagged."""
        tmpdir = tempfile.mkdtemp()
        try:
            _make_poetry_pyproject(
                tmpdir,
                ('[tool.poetry.dependencies]\npython = "^3.9"\nrequests = "*"\n'),
            )
            checker = PyprojectChecker()
            _write_covering_lock(tmpdir)
            index = {tmpdir: {"pyproject.toml", "poetry.lock"}}
            findings = checker.check(index, tmpdir)
            pin_findings = [
                f
                for f in findings
                if "lock file" not in f.message and "stale" not in f.message
            ]
            assert len(pin_findings) == 1
            assert "requests" in pin_findings[0].message
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_poetry_exact_no_operator(self):
        """ "2.28.0" (no operator) is accepted as an exact pin in Poetry."""
        tmpdir = tempfile.mkdtemp()
        try:
            _make_poetry_pyproject(
                tmpdir,
                ('[tool.poetry.dependencies]\npython = "^3.9"\nrequests = "2.28.0"\n'),
            )
            checker = PyprojectChecker()
            _write_covering_lock(tmpdir)
            index = {tmpdir: {"pyproject.toml", "poetry.lock"}}
            findings = checker.check(index, tmpdir)
            pin_findings = [
                f
                for f in findings
                if "lock file" not in f.message and "stale" not in f.message
            ]
            assert pin_findings == [], (
                "bare version string is exact in Poetry, got: {}".format(pin_findings)
            )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_poetry_cross_ref_deps_included(self):
        """Poetry deps are included in the manifest set for lock-file cross-referencing."""
        tmpdir = tempfile.mkdtemp()
        try:
            toml_content = (
                '[tool.poetry.dependencies]\npython = "^3.9"\nblack = "==23.7.0"\n'
            )
            toml_path = os.path.join(tmpdir, "pyproject.toml")
            lock_path = os.path.join(tmpdir, "poetry.lock")
            with open(toml_path, "w") as fh:
                fh.write(toml_content)
            # Lock file does NOT contain black — should trigger a stale warning
            with open(lock_path, "w") as fh:
                fh.write('[[package]]\nname = "requests"\nversion = "2.28.0"\n')
            checker = PyprojectChecker()
            _write_covering_lock(tmpdir)
            index = {tmpdir: {"pyproject.toml", "poetry.lock"}}
            findings = checker.check(index, tmpdir)
            stale = [f for f in findings if "stale" in f.message]
            assert len(stale) == 1
            assert "black" in stale[0].message
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_poetry_git_inline_table_flagged(self):
        """Git deps in inline tables should be flagged, not silently skipped."""
        tmpdir = tempfile.mkdtemp()
        try:
            _make_poetry_pyproject(
                tmpdir,
                (
                    "[tool.poetry.dependencies]\n"
                    'python = "^3.9"\n'
                    'internal-lib = { git = "https://github.com/example/internal-lib.git", rev = "abcdef" }\n'
                ),
            )
            checker = PyprojectChecker()
            _write_covering_lock(tmpdir)
            index = {tmpdir: {"pyproject.toml", "poetry.lock"}}
            findings = checker.check(index, tmpdir)
            pin_findings = [
                f
                for f in findings
                if "lock file" not in f.message and "stale" not in f.message
            ]
            assert len(pin_findings) == 1, (
                "git inline-table dep should be flagged, got: {}".format(
                    [f.message for f in pin_findings]
                )
            )
            assert "internal-lib" in pin_findings[0].message
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_poetry_git_dep_included_in_cross_ref(self):
        """Git inline-table deps should be in the manifest set for lock file cross-referencing."""
        tmpdir = tempfile.mkdtemp()
        try:
            toml_content = (
                "[tool.poetry.dependencies]\n"
                'python = "^3.9"\n'
                'internal-lib = { git = "https://github.com/example/lib.git", rev = "abc" }\n'
            )
            toml_path = os.path.join(tmpdir, "pyproject.toml")
            lock_path = os.path.join(tmpdir, "poetry.lock")
            with open(toml_path, "w") as fh:
                fh.write(toml_content)
            with open(lock_path, "w") as fh:
                fh.write('[[package]]\nname = "requests"\nversion = "2.28.0"\n')
            checker = PyprojectChecker()
            _write_covering_lock(tmpdir)
            index = {tmpdir: {"pyproject.toml", "poetry.lock"}}
            findings = checker.check(index, tmpdir)
            stale = [f for f in findings if "stale" in f.message]
            assert len(stale) == 1
            assert "internal_lib" in stale[0].message
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestEmptyLockFailsClosed:
    """Review repro M1: an empty lock must not satisfy the lock requirement."""

    def test_zero_byte_uv_lock(self):
        tmpdir = tempfile.mkdtemp()
        try:
            _make_pyproject(
                tmpdir,
                '[project]\nname="x"\nversion="1"\ndependencies=["requests==2.31.0"]\n',
            )
            open(os.path.join(tmpdir, "uv.lock"), "w").close()
            findings = PyprojectChecker().check(
                {tmpdir: {"pyproject.toml", "uv.lock"}}, tmpdir
            )
            by_path = {f.path: f for f in findings}
            assert sorted(by_path) == ["pyproject.toml", "uv.lock"]
            assert by_path["uv.lock"].message == (
                "uv.lock contains no packages (empty or unrecognised format)"
            )
            assert by_path["uv.lock"].integrity
            assert (
                "missing 1 dependency (requests)" in by_path["pyproject.toml"].message
            )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_empty_lock_beside_populated_lock(self):
        """Each empty lock is reported even when another lock covers the deps."""
        tmpdir = tempfile.mkdtemp()
        try:
            _make_pyproject(tmpdir, _PYPROJECT_WITH_DEPS)
            open(os.path.join(tmpdir, "pdm.lock"), "w").close()
            with open(os.path.join(tmpdir, "uv.lock"), "w") as fh:
                fh.write('[[package]]\nname = "flask"\nversion = "2.3.2"\n')
            findings = PyprojectChecker().check(
                {tmpdir: {"pyproject.toml", "pdm.lock", "uv.lock"}}, tmpdir
            )
            assert [
                (f.path, "contains no packages" in f.message) for f in findings
            ] == [("pdm.lock", True)]
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_missing_list_deduplicated(self):
        """A dependency in two groups is listed once."""
        tmpdir = tempfile.mkdtemp()
        try:
            _make_pyproject(
                tmpdir,
                '[project]\nname="x"\nversion="1"\ndependencies=["requests==2.31.0"]\n'
                '[dependency-groups]\ndev=["requests==2.31.0"]\n',
            )
            open(os.path.join(tmpdir, "uv.lock"), "w").close()
            findings = PyprojectChecker().check(
                {tmpdir: {"pyproject.toml", "uv.lock"}}, tmpdir
            )
            stale = [f.message for f in findings if "stale" in f.message]
            assert stale == ["lock files are stale: missing 1 dependency (requests)"]
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_empty_lock_without_deps_not_reported(self):
        tmpdir = tempfile.mkdtemp()
        try:
            _make_pyproject(tmpdir, _PYPROJECT_NO_DEPS)
            open(os.path.join(tmpdir, "uv.lock"), "w").close()
            findings = PyprojectChecker().check(
                {tmpdir: {"pyproject.toml", "uv.lock"}}, tmpdir
            )
            assert findings == []
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestPEP508DirectReferences:
    """PEP 508 direct references: package @ URL."""

    def _check_pep508(self, dep_line):
        """Run pyproject checker on a single PEP 621 dependency."""
        tmpdir = tempfile.mkdtemp()
        try:
            content = (
                '[project]\nname = "test"\ndependencies = [\n    "{}",\n]\n'.format(
                    dep_line
                )
            )
            with open(os.path.join(tmpdir, "pyproject.toml"), "w") as fh:
                fh.write(content)
            checker = PyprojectChecker()
            _write_covering_lock(tmpdir)
            index = {tmpdir: {"pyproject.toml", "poetry.lock"}}
            findings = checker.check(index, tmpdir)
            return [
                f
                for f in findings
                if "lock file" not in f.message and "stale" not in f.message
            ]
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_mutable_git_tag_flagged(self):
        """git+https://...@v0.3.0 is a movable tag, should be flagged."""
        findings = self._check_pep508(
            "lib @ git+https://github.com/example/lib.git@v0.3.0"
        )
        assert len(findings) == 1
        assert "mutable" in findings[0].message

    def test_mutable_git_branch_flagged(self):
        """git+https://...@main is a branch, should be flagged."""
        findings = self._check_pep508(
            "lib @ git+https://github.com/example/lib.git@main"
        )
        assert len(findings) == 1
        assert "mutable" in findings[0].message

    def test_git_no_ref_flagged(self):
        """git+https://... with no @ ref at all, should be flagged."""
        findings = self._check_pep508("lib @ git+https://github.com/example/lib.git")
        assert len(findings) == 1

    def test_immutable_git_sha_accepted(self):
        """git+https://...@<40-char-sha> is immutable, should be accepted."""
        findings = self._check_pep508(
            "lib @ git+https://github.com/example/lib.git@a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4"
        )
        assert len(findings) == 0

    def test_archive_url_flagged(self):
        """Plain https:// URL without hash is not content-addressed."""
        findings = self._check_pep508("lib @ https://example.com/lib-1.0.tar.gz")
        assert len(findings) == 1
        assert "URL reference" in findings[0].message

    def test_archive_url_with_fragment_hash_accepted(self):
        """https:// URL with #sha256= fragment is hash-pinned."""
        findings = self._check_pep508(
            "lib @ https://example.com/lib-1.0.tar.gz#sha256=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        )
        assert len(findings) == 0

    def test_archive_url_with_sha1_fragment_accepted(self):
        """https:// URL with #sha1= fragment is hash-pinned."""
        findings = self._check_pep508(
            "lib @ https://example.com/lib-1.0.tar.gz#sha1=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        )
        assert len(findings) == 0

    def test_archive_url_with_sha224_fragment_accepted(self):
        """https:// URL with #sha224= fragment is hash-pinned."""
        findings = self._check_pep508(
            "lib @ https://example.com/lib-1.0.tar.gz#sha224=cccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
        )
        assert len(findings) == 0

    def test_archive_url_with_hash_after_subdirectory(self):
        """#subdirectory=src&sha256=abc — hash not first in fragment."""
        findings = self._check_pep508(
            "lib @ https://example.com/lib-1.0.tar.gz#subdirectory=src&sha256=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        )
        assert len(findings) == 0


def _check_fixture(subdir_name):
    # type: (str) -> list
    """Run the checker on a fixture dir with its real lock file, as the CLI would."""
    from pinstack.core import build_index

    dirpath = os.path.join(FIXTURES, subdir_name)
    index = build_index(dirpath, set(PyprojectChecker.patterns))
    return PyprojectChecker().check(index, dirpath)


class TestWildcardVersions:
    """Review repro M4a: `==2.*` is a PEP 440 prefix match, not a pin."""

    def test_pep508_and_poetry_wildcards_flagged(self):
        findings = _check_fixture("wildcard")
        assert [(f.line, f.message) for f in findings] == [
            (4, "'requests' uses wildcard '==2.*'; pin to an exact version"),
            (7, "'flask' uses wildcard '2.*'; use exact pinning"),
        ]

    def test_poetry_double_equals_wildcard(self):
        from pinstack.checkers.pyproject import _check_poetry_version

        assert _check_poetry_version("x", "==1.2.*")[0]
        assert not _check_poetry_version("x", "==1.2.3")[0]

    def test_requirements_wildcard_is_a_pin_error(self):
        from pinstack.checkers.requirements import _analyze_line

        assert [r.kind for r in _analyze_line("requests==2.*")] == ["pin_error"]


class TestInlineTableProject:
    """Review repro M4b: PEP 621 tables written as inline tables or dotted keys."""

    def test_inline_project_table(self):
        findings = _check_fixture("inline_project")
        assert [f.message for f in findings] == [
            "'requests>=2' uses '>=' instead of '=='; use exact pinning"
        ]

    def test_multiline_inline_table(self):
        arrays = extract_dependency_arrays(
            "project = {\n"
            '  name = "x",\n'
            '  dependencies = [\n    "a>=1",  # comment\n  ],\n'
            "}\n"
            '[tool.other]\nx = ["b>=1"]\n'
        )
        assert arrays == [(["a>=1"], "dependencies")]

    def test_optional_dependencies_inline_under_project(self):
        arrays = extract_dependency_arrays(
            '[project]\nname = "x"\noptional-dependencies = { dev = ["pytest>=7"] }\n'
        )
        assert arrays == [(["pytest>=7"], "dev")]

    def test_dotted_keys(self):
        arrays = extract_dependency_arrays(
            'project.dependencies = ["a>=1"]\n'
            "[project]\n"
            'optional-dependencies.dev = ["b>=1"]\n'
        )
        assert arrays == [(["a>=1"], "dependencies"), (["b>=1"], "dev")]

    def test_dependency_groups_inline_skips_include_group(self):
        arrays = extract_dependency_arrays(
            'dependency-groups = { dev = ["pytest>=7", { include-group = "lint" }] }\n'
        )
        assert arrays == [(["pytest>=7"], "dev")]

    def test_other_inline_tables_ignored(self):
        arrays = extract_dependency_arrays(
            '[project]\nlicense = { text = "MIT" }\nurls = { home = "https://x" }\n'
            '[tool.poetry.dependencies]\nfoo = { version = "^1", extras = ["a"] }\n'
        )
        assert arrays == []


class TestPoetryDependencyForms:
    """Review repro M4c+d: table-form and multi-constraint Poetry dependencies."""

    def test_combined_repro(self):
        findings = _check_fixture("poetry_forms")
        assert [(f.line, f.message.split("'")[1]) for f in findings] == [
            (5, "foo"),
            (6, "bar"),
            (9, "baz"),
        ]

    def test_every_constraint_checked(self):
        from pinstack.checkers.pyproject import extract_poetry_dependencies

        deps = extract_poetry_dependencies(
            "[tool.poetry.dependencies]\n"
            "foo = [\n"
            '  { version = "1.0.0", python = "<3.8" },\n'
            '  { version = "^2.0", python = ">=3.8" },\n'
            '  { git = "https://x", python = ">=3.12" },\n'
            "]\n"
            'after = "1.0.0"\n'
        )
        assert deps == [
            ("foo", "1.0.0", 2),
            ("foo", "^2.0", 2),
            ("foo", "", 2),
            ("after", "1.0.0", 7),
        ]

    def test_table_form_in_groups_and_without_version(self):
        from pinstack.checkers.pyproject import extract_poetry_dependencies

        deps = extract_poetry_dependencies(
            "[tool.poetry.group.dev.dependencies.lint]\n"
            'version = "1.0.0"  # pinned\n'
            "[tool.poetry.dev-dependencies.gitdep]\n"
            'git = "https://x"\n'
            "[tool.poetry.dependencies.python]\n"
            'version = "^3.9"\n'
        )
        assert deps == [("lint", "1.0.0", 1), ("gitdep", "", 3)]


class TestPyprojectReviewFollowUps:
    """Follow-ups from the local and Codex reviews of the M4 changes."""

    def test_quoted_dotted_poetry_table_name(self):
        from pinstack.checkers.pyproject import extract_poetry_dependencies

        deps = extract_poetry_dependencies(
            '[tool.poetry.dependencies."zope.interface"]\nversion = "^5"\n'
        )
        assert deps == [("zope.interface", "^5", 1)]

    def test_poetry_inline_and_dotted_dependency_tables(self):
        from pinstack.checkers.pyproject import extract_poetry_dependencies

        deps = extract_poetry_dependencies(
            "[tool.poetry]\n"
            'dependencies = { requests = "^2", flask = { version = "2.3.2" } }\n'
            'dependencies.boto3 = "^1"\n'
            'group.dev.dependencies.pytest = "7.4.0"\n'
            'dependencies.black.version = "^23"\n'
        )
        assert deps == [
            ("requests", "^2", 2),
            ("flask", "2.3.2", 2),
            ("boto3", "^1", 3),
            ("pytest", "7.4.0", 4),
            ("black", "^23", 5),
        ]

    def test_multiline_poetry_inline_table(self):
        from pinstack.checkers.pyproject import extract_poetry_dependencies

        deps = extract_poetry_dependencies(
            "[tool.poetry.dependencies]\n"
            "foo = {\n"
            '  version = "^1",\n'
            '  extras = ["a"],\n'
            "}\n"
            'bar = "1.0.0"\n'
        )
        assert deps == [("foo", "^1", 2), ("bar", "1.0.0", 6)]

    def test_poetry_combined_constraints_flagged(self):
        from pinstack.checkers.pyproject import _check_poetry_version

        for v in ("1.0.0 || ^2.0", "1.0.0,<3", "1.0.0 <3", "1.0.0|2.0.0"):
            assert _check_poetry_version("foo", v)[0], v
        assert not _check_poetry_version("foo", "1.0.0")[0]

    def test_poetry_tables_not_read_as_pep508_arrays(self):
        arrays = extract_dependency_arrays(
            "[tool.poetry.dependencies.requests]\n"
            'version = "2.31.0"\n'
            'extras = ["security"]\n'
            "[tool.poetry.dependencies]\n"
            "foo = [\n"
            '  { version = "1.0.0", python = "<3.8" },\n'
            "]\n"
        )
        assert arrays == []

    def test_unparsable_inline_project_table_reported(self):
        """A dependency table this parser cannot read must not pass silently."""
        arrays = extract_dependency_arrays('project = { dependencies = ["a>=1" }\n')
        assert len(arrays) == 1
        from pinstack.checkers.pyproject import _check_dep_specifier

        assert _check_dep_specifier(arrays[0][0][0])[0]

    def test_spaced_and_quoted_keys(self):
        assert extract_dependency_arrays(
            'project = { optional-dependencies . dev = ["a>=1"] }\n'
        ) == [(["a>=1"], "dev")]
        assert extract_dependency_arrays(
            '"project" = { dependencies = ["b>=1"] }\n'
        ) == [(["b>=1"], "dependencies")]
        assert extract_dependency_arrays(
            'project."optional-dependencies".dev = ["c>=1"]\n'
        ) == [(["c>=1"], "dev")]

    def test_multi_constraint_package_listed_once_when_stale(self):
        tmpdir = tempfile.mkdtemp()
        try:
            _make_pyproject(
                tmpdir,
                "[tool.poetry.dependencies]\n"
                'foo = [{ version = "1.0.0", python = "<3.8" },'
                ' { version = "1.0.0", python = ">=3.8" }]\n',
            )
            with open(os.path.join(tmpdir, "poetry.lock"), "w") as fh:
                fh.write('[[package]]\nname = "other"\n')
            findings = PyprojectChecker().check(
                {tmpdir: {"pyproject.toml", "poetry.lock"}}, tmpdir
            )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        assert [f.message for f in findings] == [
            "lock files are stale: missing 1 dependency (foo)"
        ]

    def test_dotted_source_fields_declare_a_dependency(self):
        """Codex #14 follow-up: `foo.git` + `foo.rev` is `foo = { git, rev }`."""
        tmpdir = tempfile.mkdtemp()
        try:
            _make_pyproject(
                tmpdir,
                "[tool.poetry.dependencies]\n"
                'foo.git = "https://github.com/u/r.git"\n'
                'foo.rev = "main"\n',
            )
            findings = PyprojectChecker().check({tmpdir: {"pyproject.toml"}}, tmpdir)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        assert "'foo' has no version constraint; use exact pinning" in [
            f.message for f in findings
        ]

    def test_dotted_fields_merge_into_one_entry(self):
        from pinstack.checkers.pyproject import extract_poetry_dependencies

        deps = extract_poetry_dependencies(
            "[tool.poetry.dependencies]\n"
            'foo.version = "1.0.0"\n'
            'foo.extras = ["a"]\n'
            'bar = "2.0.0"\n'
            'baz.path = "../baz"\n'
        )
        assert deps == [("foo", "1.0.0", 2), ("bar", "2.0.0", 4), ("baz", "", 5)]

    def test_space_after_exact_operator(self):
        """Codex #14 follow-up: Poetry accepts `== 1.2.3` as one exact constraint."""
        tmpdir = tempfile.mkdtemp()
        try:
            _make_pyproject(tmpdir, '[tool.poetry.dependencies]\nfoo = "== 1.2.3"\n')
            with open(os.path.join(tmpdir, "poetry.lock"), "w") as fh:
                fh.write('[[package]]\nname = "foo"\nversion = "1.2.3"\n')
            findings = PyprojectChecker().check(
                {tmpdir: {"pyproject.toml", "poetry.lock"}}, tmpdir
            )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        assert findings == []

    def test_space_after_range_operator_still_flagged(self):
        from pinstack.checkers.pyproject import _check_poetry_version

        assert _check_poetry_version("foo", ">= 1.2.3")[0]
        assert _check_poetry_version("foo", "== 1.2.3 || ^2")[0]
