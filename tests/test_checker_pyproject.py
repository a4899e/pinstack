"""Tests for the pyproject checker."""

import os
import tempfile
import shutil

import pytest

from pinstack.core import Severity
from pinstack.checkers.pyproject import PyprojectChecker, extract_dependency_arrays

# Base directory for pyproject fixtures — each case lives in a named subdir
# containing a pyproject.toml file so the checker's pattern filter passes.
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "pyproject")


def _check_subdir(subdir_name):
    # type: (str) -> list
    """Run PyprojectChecker against the pyproject.toml in a named fixture subdir."""
    checker = PyprojectChecker()
    dirpath = os.path.join(FIXTURES, subdir_name)
    # Include a lock file in the index so tests focused on pinning checks don't
    # also pick up the companion-lock-file warning.
    index = {dirpath: {"pyproject.toml", "poetry.lock"}}
    return checker.check(index, dirpath)


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
            '[project]\n'
            'dependencies = [\n'
            '    "flask==2.3.2",\n'
            '    "requests==2.31.0",\n'
            ']\n'
        )
        arrays = extract_dependency_arrays(content)
        assert len(arrays) == 1
        deps, label = arrays[0]
        assert "flask==2.3.2" in deps
        assert "requests==2.31.0" in deps

    def test_inline_comments_stripped(self):
        content = (
            '[project]\n'
            'dependencies = [\n'
            '    "flask==2.3.2",  # web framework\n'
            '    "requests==2.31.0",  # http\n'
            ']\n'
        )
        arrays = extract_dependency_arrays(content)
        assert len(arrays) == 1
        deps, _ = arrays[0]
        assert "flask==2.3.2" in deps
        assert "requests==2.31.0" in deps

    def test_optional_dependencies_sections(self):
        content = (
            '[project.optional-dependencies]\n'
            'dev = [\n'
            '    "pytest==7.4.0",\n'
            ']\n'
            'docs = [\n'
            '    "sphinx==7.1.0",\n'
            ']\n'
        )
        arrays = extract_dependency_arrays(content)
        assert len(arrays) == 2
        all_deps = []
        for deps, label in arrays:
            all_deps.extend(deps)
        assert "pytest==7.4.0" in all_deps
        assert "sphinx==7.1.0" in all_deps

    def test_ignores_classifiers_array(self):
        content = (
            '[project]\n'
            'classifiers = [\n'
            '    "Development Status :: 3 - Alpha",\n'
            ']\n'
            'dependencies = [\n'
            '    "flask==2.3.2",\n'
            ']\n'
        )
        arrays = extract_dependency_arrays(content)
        assert len(arrays) == 1
        deps, _ = arrays[0]
        assert "flask==2.3.2" in deps
        # classifier text should NOT be in deps
        assert not any("Development Status" in d for d in deps)

    def test_empty_array(self):
        content = '[project]\ndependencies = []\n'
        arrays = extract_dependency_arrays(content)
        # Either returns empty list or a single entry with empty deps
        total_deps = sum(len(deps) for deps, _ in arrays)
        assert total_deps == 0

    def test_no_section_header(self):
        """Dependencies declared before any section header are not in [project]."""
        content = 'dependencies = [\n    "flask==2.3.2",\n]\n'
        arrays = extract_dependency_arrays(content)
        # Should return no arrays (not in a recognised section)
        assert len(arrays) == 0

    def test_trailing_comma_handled(self):
        content = (
            '[project]\n'
            'dependencies = [\n'
            '    "flask==2.3.2",\n'
            '    "requests==2.31.0",\n'  # trailing comma on last item
            ']\n'
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
            '[project]\n'
            'dependencies = ["flask==2.3.2"]\n'
            '\n'
            '[project.optional-dependencies]\n'
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
            '[build-system]\n'
            'requires = ["setuptools>=61.0", "wheel"]\n'
            'build-backend = "setuptools.build_meta"\n'
            '\n'
            '[project]\n'
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
        assert findings == [], "All ==pins should produce 0 findings, got: {}".format(findings)

    def test_checker_name(self):
        checker = PyprojectChecker()
        assert checker.name == "pyproject"

    def test_checker_patterns(self):
        checker = PyprojectChecker()
        assert "pyproject.toml" in checker.patterns


class TestPyprojectCheckerBad:
    def test_bad_toml_has_findings(self):
        findings = _check_subdir("bad")
        assert len(findings) == 3, "flask>=, requests~=, certifi (bare) should give 3 findings"

    def test_bad_toml_all_errors(self):
        findings = _check_subdir("bad")
        for f in findings:
            assert f.severity == Severity.ERROR

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

    def test_inline_finding_is_error(self):
        findings = _check_subdir("inline")
        assert findings[0].severity == Severity.ERROR


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
                    '[project.optional-dependencies]\n'
                    'dev = [\n'
                    '    "pytest>=7.0",\n'
                    '    "black==23.7.0",\n'
                    ']\n'
                )
            checker = PyprojectChecker()
            index = {tmpdir: {"pyproject.toml", "poetry.lock"}}
            findings = checker.check(index, tmpdir)
            assert len(findings) == 1
            assert "pytest" in findings[0].message
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Lock file companion check tests
# ---------------------------------------------------------------------------

_PYPROJECT_WITH_DEPS = (
    '[project]\n'
    'name = "myapp"\n'
    'version = "1.0.0"\n'
    'dependencies = [\n'
    '    "flask==2.3.2",\n'
    ']\n'
)

_PYPROJECT_NO_DEPS = (
    '[project]\n'
    'name = "myapp"\n'
    'version = "1.0.0"\n'
)


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
            lock_warnings = [
                f for f in findings
                if "lock file" in f.message
            ]
            assert len(lock_warnings) == 1
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_requirements_txt_satisfies(self):
        tmpdir = tempfile.mkdtemp()
        try:
            _make_pyproject(tmpdir, _PYPROJECT_WITH_DEPS)
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
            checker = PyprojectChecker()
            index = {tmpdir: {"pyproject.toml", "pdm.lock"}}
            findings = checker.check(index, tmpdir)
            lock_warnings = [f for f in findings if "lock file" in f.message]
            assert lock_warnings == []
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_uv_lock_satisfies(self):
        tmpdir = tempfile.mkdtemp()
        try:
            _make_pyproject(tmpdir, _PYPROJECT_WITH_DEPS)
            checker = PyprojectChecker()
            index = {tmpdir: {"pyproject.toml", "uv.lock"}}
            findings = checker.check(index, tmpdir)
            lock_warnings = [f for f in findings if "lock file" in f.message]
            assert lock_warnings == []
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

    def test_lock_file_error_is_error_severity(self):
        tmpdir = tempfile.mkdtemp()
        try:
            _make_pyproject(tmpdir, _PYPROJECT_WITH_DEPS)
            checker = PyprojectChecker()
            index = {tmpdir: {"pyproject.toml"}}
            findings = checker.check(index, tmpdir)
            lock_errors = [f for f in findings if "lock file" in f.message]
            assert len(lock_errors) == 1
            assert lock_errors[0].severity == Severity.ERROR
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
