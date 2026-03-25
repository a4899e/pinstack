"""CLI tests — run via subprocess so sys.exit() is isolated."""

import json
import os
import subprocess
import sys
import tempfile


PYTHON = sys.executable


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(args, cwd=None):
    # type: (list, str) -> subprocess.CompletedProcess
    cmd = [PYTHON, "-m", "pinstack"] + args
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=cwd or PROJECT_ROOT,
    )


def _empty_dir():
    # type: () -> str
    return tempfile.mkdtemp()


# ---------------------------------------------------------------------------
# --version
# ---------------------------------------------------------------------------

class TestVersionFlag:
    def test_version_flag(self):
        result = _run(["--version"])
        assert result.returncode == 0
        assert "pinstack" in result.stdout
        # version string should contain a digit
        assert any(ch.isdigit() for ch in result.stdout)


# ---------------------------------------------------------------------------
# --list-checkers
# ---------------------------------------------------------------------------

class TestListCheckers:
    def test_list_checkers_exits_zero(self):
        result = _run(["--list-checkers"])
        assert result.returncode == 0

    def test_list_checkers_does_not_crash(self):
        result = _run(["--list-checkers"])
        # No Python traceback in output
        assert "Traceback" not in result.stderr


# ---------------------------------------------------------------------------
# Mutually exclusive --check / --exclude
# ---------------------------------------------------------------------------

class TestCheckExcludeMutex:
    def test_check_and_exclude_mutually_exclusive(self):
        d = _empty_dir()
        try:
            result = _run(["--check", "foo", "--exclude", "bar", d])
            assert result.returncode == 2
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# Text format (default)
# ---------------------------------------------------------------------------

class TestDefaultFormatText:
    def test_empty_dir_shows_zero_findings(self):
        d = _empty_dir()
        try:
            result = _run([d])
            assert result.returncode == 0
            assert "0 findings" in result.stdout
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    def test_default_format_is_text(self):
        d = _empty_dir()
        try:
            result = _run([d])
            assert result.returncode == 0
            # Text output always ends with a summary line
            assert "findings" in result.stdout or "error" in result.stdout or "warning" in result.stdout
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# SARIF format
# ---------------------------------------------------------------------------

class TestSarifFormat:
    def test_sarif_valid_json(self):
        d = _empty_dir()
        try:
            result = _run(["--format", "sarif", d])
            assert result.returncode == 0
            data = json.loads(result.stdout)
            assert "$schema" in data
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    def test_sarif_schema_url(self):
        d = _empty_dir()
        try:
            result = _run(["--format", "sarif", d])
            assert result.returncode == 0
            data = json.loads(result.stdout)
            assert "sarif" in data["$schema"].lower()
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    def test_sarif_version_field(self):
        d = _empty_dir()
        try:
            result = _run(["--format", "sarif", d])
            data = json.loads(result.stdout)
            assert data["version"] == "2.1.0"
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    def test_sarif_runs_structure(self):
        d = _empty_dir()
        try:
            result = _run(["--format", "sarif", d])
            data = json.loads(result.stdout)
            assert "runs" in data
            assert len(data["runs"]) == 1
            run = data["runs"][0]
            assert "tool" in run
            assert "results" in run
            assert run["tool"]["driver"]["name"] == "pinstack"
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# --exit-zero
# ---------------------------------------------------------------------------

class TestExitZeroFlag:
    def test_exit_zero_accepted(self):
        d = _empty_dir()
        try:
            result = _run(["--exit-zero", d])
            assert result.returncode == 0
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    def test_exit_zero_no_crash(self):
        d = _empty_dir()
        try:
            result = _run(["--exit-zero", d])
            assert "Traceback" not in result.stderr
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# Unknown checker name
# ---------------------------------------------------------------------------

class TestUnknownCheckerName:
    def test_unknown_checker_exits_2(self):
        d = _empty_dir()
        try:
            result = _run(["--check", "no_such_checker_xyz", d])
            assert result.returncode == 2
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    def test_unknown_checker_message_in_stderr(self):
        d = _empty_dir()
        try:
            result = _run(["--check", "no_such_checker_xyz", d])
            assert "Unknown checker" in result.stderr or "unknown checker" in result.stderr.lower()
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# Non-existent path
# ---------------------------------------------------------------------------

class TestNonexistentPath:
    def test_nonexistent_path_exits_2(self):
        result = _run(["/tmp/this_path_does_not_exist_pinstack_test_xyz"])
        assert result.returncode == 2

    def test_nonexistent_path_error_in_stderr(self):
        result = _run(["/tmp/this_path_does_not_exist_pinstack_test_xyz"])
        assert "Error" in result.stderr or "error" in result.stderr.lower()


# ---------------------------------------------------------------------------
# --max-depth flag
# ---------------------------------------------------------------------------

class TestMaxDepthFlag:
    def test_max_depth_accepted(self):
        d = _empty_dir()
        try:
            result = _run(["--max-depth", "2", d])
            assert result.returncode == 0
            assert "Traceback" not in result.stderr
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    def test_max_depth_1(self):
        d = _empty_dir()
        try:
            result = _run(["--max-depth", "1", d])
            assert result.returncode == 0
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# --max-files flag
# ---------------------------------------------------------------------------

class TestMaxFilesFlag:
    def test_max_files_accepted(self):
        d = _empty_dir()
        try:
            result = _run(["--max-files", "100", d])
            assert result.returncode == 0
            assert "Traceback" not in result.stderr
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    def test_max_files_small_value(self):
        d = _empty_dir()
        try:
            result = _run(["--max-files", "1", d])
            assert result.returncode == 0
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)
