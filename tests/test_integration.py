"""End-to-end CLI integration tests for pinstack.

Each test creates a temporary directory with realistic project files and
runs pinstack via subprocess, exercising the full stack: file discovery,
checker dispatch, text/SARIF formatting, and exit codes.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# Runner helper
# ---------------------------------------------------------------------------


def run_pinstack(*args):
    # type: (*str) -> tuple
    """Run pinstack as a subprocess, return (returncode, stdout, stderr)."""
    cmd = [sys.executable, "-m", "pinstack"] + list(args)
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT_ROOT)
    return proc.returncode, proc.stdout, proc.stderr


# ---------------------------------------------------------------------------
# File-creation helpers
# ---------------------------------------------------------------------------


def _write(path, content):
    # type: (str, str) -> None
    """Write *content* to *path*, creating parent dirs as needed."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def _tmpdir():
    # type: () -> str
    return tempfile.mkdtemp()


# ---------------------------------------------------------------------------
# Fixtures: good/bad project files
# ---------------------------------------------------------------------------

_GOOD_REQUIREMENTS = (
    "# pinned with == and --hash\n"
    "requests==2.31.0 "
    "--hash=sha256:942c5a758f98d790eaed1a29cb6eefc7ffb0d1cf7af05c3d2791656dbd6ad1e1\n"
    "urllib3==2.0.7 "
    "--hash=sha256:c97959a1b29a759d727e64bd99218db638204f6e69a893d4c57a8b534cc8e3ee\n"
)

_BAD_REQUIREMENTS = "# missing pin and hash\nrequests>=2.0\nurllib3\n"

# A pyproject.toml with properly == pinned production deps
_GOOD_PYPROJECT = """\
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[project]
name = "myapp"
version = "1.0.0"
dependencies = [
    "requests==2.31.0",
    "urllib3==2.0.7",
]
"""

# A pyproject.toml with unpinned optional-dependencies (triggers pyproject checker)
_BAD_PYPROJECT = """\
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[project]
name = "myapp"
version = "1.0.0"
dependencies = []

[project.optional-dependencies]
dev = ["pytest", "ruff"]
"""

_BAD_DOCKERFILE = "FROM python:3.11\nRUN pip install requests\n"

_GOOD_DOCKERFILE = (
    "FROM python:3.11"
    "@sha256:92db8abf86e2dd56bf7b1b8b3c1e1e0e1e0e1e0e1e0e1e0e1e0e1e0e1e0e1e0e\n"
    "RUN pip install requests\n"
)


# ---------------------------------------------------------------------------
# test_clean_python_project
# ---------------------------------------------------------------------------


class TestCleanPythonProject:
    def test_exit_zero(self):
        d = _tmpdir()
        try:
            _write(os.path.join(d, "requirements.txt"), _GOOD_REQUIREMENTS)
            _write(os.path.join(d, "pyproject.toml"), _GOOD_PYPROJECT)
            rc, out, err = run_pinstack(d)
            assert rc == 0
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_zero_findings_in_output(self):
        d = _tmpdir()
        try:
            _write(os.path.join(d, "requirements.txt"), _GOOD_REQUIREMENTS)
            _write(os.path.join(d, "pyproject.toml"), _GOOD_PYPROJECT)
            rc, out, err = run_pinstack(d)
            assert "0 findings" in out
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_no_traceback(self):
        d = _tmpdir()
        try:
            _write(os.path.join(d, "requirements.txt"), _GOOD_REQUIREMENTS)
            _write(os.path.join(d, "pyproject.toml"), _GOOD_PYPROJECT)
            rc, out, err = run_pinstack(d)
            assert "Traceback" not in err
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# test_bad_python_project
# ---------------------------------------------------------------------------


class TestBadPythonProject:
    def test_exit_one(self):
        d = _tmpdir()
        try:
            _write(os.path.join(d, "requirements.txt"), _BAD_REQUIREMENTS)
            _write(os.path.join(d, "pyproject.toml"), _BAD_PYPROJECT)
            rc, out, err = run_pinstack(d)
            assert rc == 1
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_fail_lines_in_output(self):
        d = _tmpdir()
        try:
            _write(os.path.join(d, "requirements.txt"), _BAD_REQUIREMENTS)
            _write(os.path.join(d, "pyproject.toml"), _BAD_PYPROJECT)
            rc, out, err = run_pinstack(d)
            assert "FAIL" in out
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_requirements_findings_present(self):
        d = _tmpdir()
        try:
            _write(os.path.join(d, "requirements.txt"), _BAD_REQUIREMENTS)
            rc, out, err = run_pinstack(d)
            assert "requirements.txt" in out
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# test_bad_dockerfile
# ---------------------------------------------------------------------------


class TestBadDockerfile:
    def test_exit_one(self):
        d = _tmpdir()
        try:
            _write(os.path.join(d, "Dockerfile"), _BAD_DOCKERFILE)
            rc, out, err = run_pinstack(d)
            assert rc == 1

        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_fail_in_output(self):
        d = _tmpdir()
        try:
            _write(os.path.join(d, "Dockerfile"), _BAD_DOCKERFILE)
            rc, out, err = run_pinstack(d)
            assert "FAIL" in out
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_dockerfile_mentioned(self):
        d = _tmpdir()
        try:
            _write(os.path.join(d, "Dockerfile"), _BAD_DOCKERFILE)
            rc, out, err = run_pinstack(d)
            assert "Dockerfile" in out
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# test_sarif_output_valid_json
# ---------------------------------------------------------------------------


class TestSarifOutputValidJson:
    def test_valid_sarif_json(self):
        d = _tmpdir()
        try:
            _write(os.path.join(d, "requirements.txt"), _BAD_REQUIREMENTS)
            rc, out, err = run_pinstack(d, "--format", "sarif")
            assert rc == 1
            data = json.loads(out)
            assert "$schema" in data
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_sarif_results_nonempty(self):
        d = _tmpdir()
        try:
            _write(os.path.join(d, "requirements.txt"), _BAD_REQUIREMENTS)
            rc, out, err = run_pinstack(d, "--format", "sarif")
            data = json.loads(out)
            assert len(data["runs"][0]["results"]) > 0
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_sarif_version_2_1_0(self):
        d = _tmpdir()
        try:
            _write(os.path.join(d, "requirements.txt"), _BAD_REQUIREMENTS)
            rc, out, err = run_pinstack(d, "--format", "sarif")
            data = json.loads(out)
            assert data["version"] == "2.1.0"
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_sarif_tool_name_pinstack(self):
        d = _tmpdir()
        try:
            _write(os.path.join(d, "requirements.txt"), _BAD_REQUIREMENTS)
            rc, out, err = run_pinstack(d, "--format", "sarif")
            data = json.loads(out)
            assert data["runs"][0]["tool"]["driver"]["name"] == "pinstack"
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# test_exit_zero_overrides
# ---------------------------------------------------------------------------


class TestExitZeroOverrides:
    def test_exit_zero_with_findings(self):
        d = _tmpdir()
        try:
            _write(os.path.join(d, "requirements.txt"), _BAD_REQUIREMENTS)
            rc, out, err = run_pinstack(d, "--exit-zero")
            assert rc == 0
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_findings_still_reported(self):
        d = _tmpdir()
        try:
            _write(os.path.join(d, "requirements.txt"), _BAD_REQUIREMENTS)
            rc, out, err = run_pinstack(d, "--exit-zero")
            # Output should still contain findings even though exit is 0
            assert "FAIL" in out
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_exit_zero_clean_project_still_zero(self):
        d = _tmpdir()
        try:
            _write(os.path.join(d, "requirements.txt"), _GOOD_REQUIREMENTS)
            rc, out, err = run_pinstack(d, "--exit-zero")
            assert rc == 0
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# test_check_flag_limits_checkers
# ---------------------------------------------------------------------------


class TestCheckFlagLimitsCheckers:
    def test_only_requirements_findings(self):
        d = _tmpdir()
        try:
            _write(os.path.join(d, "requirements.txt"), _BAD_REQUIREMENTS)
            _write(os.path.join(d, "pyproject.toml"), _BAD_PYPROJECT)
            _write(os.path.join(d, "Dockerfile"), _BAD_DOCKERFILE)
            rc, out, err = run_pinstack(d, "--check", "requirements")
            # There should be findings from requirements
            assert rc == 1
            assert "requirements.txt" in out
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_no_pyproject_findings_when_checking_requirements_only(self):
        d = _tmpdir()
        try:
            _write(os.path.join(d, "requirements.txt"), _BAD_REQUIREMENTS)
            _write(os.path.join(d, "pyproject.toml"), _BAD_PYPROJECT)
            rc, out, err = run_pinstack(d, "--check", "requirements")
            # pyproject.toml lines should NOT appear in the output findings
            lines_with_pyproject = [
                ln for ln in out.splitlines() if "pyproject.toml" in ln and "FAIL" in ln
            ]
            assert lines_with_pyproject == []
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_no_dockerfile_findings_when_checking_requirements_only(self):
        d = _tmpdir()
        try:
            _write(os.path.join(d, "requirements.txt"), _GOOD_REQUIREMENTS)
            _write(os.path.join(d, "Dockerfile"), _BAD_DOCKERFILE)
            rc, out, err = run_pinstack(d, "--check", "requirements")
            assert "Dockerfile" not in out or "FAIL" not in out
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# test_exclude_flag
# ---------------------------------------------------------------------------


class TestExcludeFlag:
    def test_excluding_requirements_leaves_other_findings(self):
        d = _tmpdir()
        try:
            _write(os.path.join(d, "requirements.txt"), _BAD_REQUIREMENTS)
            _write(os.path.join(d, "Dockerfile"), _BAD_DOCKERFILE)
            rc, out, err = run_pinstack(d, "--exclude", "requirements")
            # Dockerfile findings should still appear
            assert "Dockerfile" in out
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_excluded_checker_findings_absent(self):
        d = _tmpdir()
        try:
            _write(os.path.join(d, "requirements.txt"), _BAD_REQUIREMENTS)
            _write(os.path.join(d, "Dockerfile"), _BAD_DOCKERFILE)
            rc, out, err = run_pinstack(d, "--exclude", "requirements")
            # requirements.txt lines should NOT be in findings
            finding_lines = [
                ln
                for ln in out.splitlines()
                if "requirements.txt" in ln and "FAIL" in ln
            ]
            assert finding_lines == []
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_exclude_multiple_checkers(self):
        d = _tmpdir()
        try:
            _write(os.path.join(d, "requirements.txt"), _BAD_REQUIREMENTS)
            _write(os.path.join(d, "pyproject.toml"), _BAD_PYPROJECT)
            _write(os.path.join(d, "Dockerfile"), _BAD_DOCKERFILE)
            rc, out, err = run_pinstack(d, "--exclude", "requirements,pyproject")
            # Neither requirements.txt nor pyproject.toml finding lines should appear
            for ln in out.splitlines():
                if "FAIL" in ln:
                    assert "requirements.txt" not in ln
                    assert "pyproject.toml" not in ln
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# test_max_depth_limits_scan
# ---------------------------------------------------------------------------


class TestMaxDepthLimitsScan:
    def _create_nested_dockerfile(self, base):
        # type: (str) -> str
        """Create base/level1/level2/level3/Dockerfile (bad) and return base."""
        nested = os.path.join(base, "level1", "level2", "level3")
        _write(os.path.join(nested, "Dockerfile"), _BAD_DOCKERFILE)
        return base

    def test_shallow_depth_misses_nested_file(self):
        d = _tmpdir()
        try:
            self._create_nested_dockerfile(d)
            # level3 is at depth 3 from base; max-depth=2 should not reach it
            rc, out, err = run_pinstack(d, "--max-depth", "2")
            assert rc == 0
            assert "Dockerfile" not in out or "FAIL" not in out
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_deep_depth_finds_nested_file(self):
        d = _tmpdir()
        try:
            self._create_nested_dockerfile(d)
            # max-depth=4 should reach level3/Dockerfile
            rc, out, err = run_pinstack(d, "--max-depth", "4")
            assert rc == 1
            assert "Dockerfile" in out
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_boundary_depth_three_finds_file(self):
        d = _tmpdir()
        try:
            self._create_nested_dockerfile(d)
            # level3 is depth 3 (level1=1, level2=2, level3=3); max-depth=4 includes depth 3
            rc, out, err = run_pinstack(d, "--max-depth", "4")
            assert "FAIL" in out
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# test_list_checkers
# ---------------------------------------------------------------------------


class TestListCheckers:
    # The 16 canonical checker names as registered
    EXPECTED_NAMES = {
        "cargo",
        "compose",
        "dockerfile",
        "gemfile",
        "github_actions",
        "go",
        "gradle",
        "helm",
        "maven",
        "package_json",
        "package_lock",
        "pnpm_lock",
        "pyproject",
        "requirements",
        "terraform",
        "yarn_lock",
    }

    def test_exit_zero(self):
        rc, out, err = run_pinstack("--list-checkers")
        assert rc == 0

    def test_all_16_checkers_listed(self):
        rc, out, err = run_pinstack("--list-checkers")
        # Each line starts with the checker name followed by whitespace
        listed = set()
        for line in out.splitlines():
            parts = line.split()
            if parts:
                listed.add(parts[0])
        assert self.EXPECTED_NAMES == listed

    def test_no_traceback(self):
        rc, out, err = run_pinstack("--list-checkers")
        assert "Traceback" not in err


# ---------------------------------------------------------------------------
# test_self_scan
# ---------------------------------------------------------------------------


class TestSelfScan:
    def test_self_scan_runs_without_crash(self):
        rc, out, err = run_pinstack(PROJECT_ROOT)
        assert "Traceback" not in err

    def test_self_scan_finds_fixture_findings(self):
        """pinstack's test fixtures contain intentionally bad files."""
        rc, out, err = run_pinstack(PROJECT_ROOT)
        # Should find findings from test fixtures (bad requirements files)
        assert rc == 1

    def test_self_scan_clean_with_fixtures_excluded(self):
        """With fixtures excluded, pinstack's pyproject.toml has no companion lock file."""
        rc, out, err = run_pinstack(PROJECT_ROOT, "--exclude-dir", "fixtures")
        # pinstack's own deps are pinned and requirements.txt has hashes
        assert rc == 0
        assert "0 findings" in out

    def test_self_scan_sarif_is_valid_json(self):
        rc, out, err = run_pinstack(PROJECT_ROOT, "--format", "sarif")
        data = json.loads(out)
        assert "runs" in data
        assert data["version"] == "2.1.0"


# ---------------------------------------------------------------------------
# --exclude-dir
# ---------------------------------------------------------------------------


class TestExcludeDir:
    def test_exclude_dir_skips_directory(self):
        with tempfile.TemporaryDirectory() as d:
            # Create a bad requirements.txt inside a subdir called "checkouts"
            subdir = os.path.join(d, "checkouts")
            os.makedirs(subdir)
            with open(os.path.join(subdir, "requirements.txt"), "w") as f:
                f.write("requests\n")
            # Also create one at the root
            with open(os.path.join(d, "requirements.txt"), "w") as f:
                f.write("flask\n")
            # Without --exclude-dir, both are found
            rc, out, _ = run_pinstack(d)
            assert rc == 1
            assert "checkouts" in out
            # With --exclude-dir, only root is found
            rc2, out2, _ = run_pinstack("--exclude-dir", "checkouts", d)
            assert rc2 == 1
            assert "checkouts" not in out2
            assert "requirements.txt" in out2

    def test_exclude_dir_multiple(self):
        with tempfile.TemporaryDirectory() as d:
            for dirname in ["build", "dist"]:
                subdir = os.path.join(d, dirname)
                os.makedirs(subdir)
                with open(os.path.join(subdir, "requirements.txt"), "w") as f:
                    f.write("requests\n")
            rc, out, _ = run_pinstack("--exclude-dir", "build,dist", d)
            assert rc == 0
            assert "0 findings" in out
