"""Tests for pinstack.core — FileIndex, Finding, Checker, CheckerRegistry, runner, formatter."""

import os
import tempfile

import pytest

from pinstack.core import (
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_INDEX_SIZE,
    Checker,
    CheckerRegistry,
    Finding,
    _matches_any_pattern,
    build_index,
    format_text,
    run_checkers,
    validate_url_fragment_hashes,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

class DummyChecker(Checker):
    name = "dummy"
    description = "A dummy checker for tests"
    patterns = ["dummy.txt"]  # type: ignore[assignment]

    def check(self, index, root):
        findings = []
        for dirpath, filenames in index.items():
            if "dummy.txt" in filenames:
                rel = os.path.relpath(os.path.join(dirpath, "dummy.txt"), root)
                findings.append(
                    Finding(
                        checker=self.name,
                        path=rel,
                        line=1,
                        message="dummy finding",
                    )
                )
        return findings


class CrashingChecker(Checker):
    name = "crashing"
    description = "Always crashes"
    patterns = ["crash.txt"]  # type: ignore[assignment]

    def check(self, index, root):
        raise RuntimeError("boom")


def _make_tmpdir_tree(structure):
    # type: (dict) -> str
    """
    Create a temp directory from a nested dict.
    Keys ending in '/' are sub-directories; other keys are files whose value
    is the file content (str).  Returns the root path.
    """
    root = tempfile.mkdtemp()
    _populate(root, structure)
    return root


def _populate(base, structure):
    # type: (str, dict) -> None
    for name, value in structure.items():
        path = os.path.join(base, name)
        if isinstance(value, dict):
            os.makedirs(path, exist_ok=True)
            _populate(path, value)
        else:
            # Ensure parent exists
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as fh:
                fh.write(value)


# ---------------------------------------------------------------------------
# Finding
# ---------------------------------------------------------------------------

class TestFinding:
    def test_finding_fields(self):
        f = Finding(
            checker="test",
            path="some/path/file.txt",
            line=42,
            message="something is wrong",
        )
        assert f.checker == "test"
        assert f.path == "some/path/file.txt"
        assert f.line == 42
        assert f.message == "something is wrong"

    def test_finding_line_zero(self):
        f = Finding(
            checker="test",
            path="Dockerfile",
            line=0,
            message="no specific line",
        )
        assert f.line == 0


# ---------------------------------------------------------------------------
# Checker base class
# ---------------------------------------------------------------------------

class TestChecker:
    def test_checker_has_name_description_patterns(self):
        c = DummyChecker()
        assert c.name == "dummy"
        assert c.description == "A dummy checker for tests"
        assert c.patterns == ["dummy.txt"]

    def test_checker_check_returns_findings(self):
        root = _make_tmpdir_tree({"dummy.txt": ""})
        try:
            c = DummyChecker()
            index = {root: {"dummy.txt"}}
            results = c.check(index, root)
            assert len(results) == 1
            assert isinstance(results[0], Finding)
            assert results[0].checker == "dummy"
        finally:
            import shutil
            shutil.rmtree(root, ignore_errors=True)

    def test_checker_base_check_raises(self):
        c = Checker()
        with pytest.raises(NotImplementedError):
            c.check({}, "/tmp")


# ---------------------------------------------------------------------------
# CheckerRegistry
# ---------------------------------------------------------------------------

class TestCheckerRegistry:
    def _make_registry(self):
        reg = CheckerRegistry()
        reg.register(DummyChecker)
        return reg

    def test_registry_register_and_list(self):
        reg = self._make_registry()
        checkers = reg.get_all()
        assert len(checkers) == 1
        assert isinstance(checkers[0], DummyChecker)

    def test_registry_get_by_name(self):
        reg = self._make_registry()
        checkers = reg.get_by_names(["dummy"])
        assert len(checkers) == 1
        assert isinstance(checkers[0], DummyChecker)

    def test_registry_get_by_name_unknown_raises(self):
        reg = self._make_registry()
        with pytest.raises(ValueError, match="Unknown checker"):
            reg.get_by_names(["nonexistent"])

    def test_registry_exclude(self):
        reg = CheckerRegistry()
        reg.register(DummyChecker)
        reg.register(CrashingChecker)
        checkers = reg.get_all(exclude=["dummy"])
        names = [c.name for c in checkers]
        assert "dummy" not in names
        assert "crashing" in names

    def test_registry_all_names(self):
        reg = CheckerRegistry()
        reg.register(DummyChecker)
        reg.register(CrashingChecker)
        names = reg.all_names()
        assert sorted(names) == names  # must be sorted
        assert "dummy" in names
        assert "crashing" in names

    def test_registry_get_all_patterns(self):
        reg = CheckerRegistry()
        reg.register(DummyChecker)
        reg.register(CrashingChecker)
        patterns = reg.get_all_patterns()
        assert "dummy.txt" in patterns
        assert "crash.txt" in patterns

    def test_registry_get_all_patterns_subset(self):
        reg = CheckerRegistry()
        reg.register(DummyChecker)
        reg.register(CrashingChecker)
        checkers = [DummyChecker()]
        patterns = reg.get_all_patterns(checkers=checkers)
        assert "dummy.txt" in patterns
        assert "crash.txt" not in patterns

    def test_registry_get_all_sorted(self):
        """get_all() returns checkers sorted by name."""
        reg = CheckerRegistry()
        reg.register(CrashingChecker)
        reg.register(DummyChecker)
        names = [c.name for c in reg.get_all()]
        assert names == sorted(names)


# ---------------------------------------------------------------------------
# _matches_any_pattern
# ---------------------------------------------------------------------------

class TestMatchesAnyPattern:
    def test_matches_exact_name(self):
        assert _matches_any_pattern("requirements.txt", {"requirements.txt"})

    def test_matches_glob_pattern(self):
        assert _matches_any_pattern("Dockerfile.prod", {"Dockerfile*"})
        assert _matches_any_pattern("Dockerfile", {"Dockerfile*"})

    def test_no_match(self):
        assert not _matches_any_pattern("setup.py", {"requirements.txt", "Dockerfile*"})

    def test_matches_extension_glob(self):
        assert _matches_any_pattern("service.dockerfile", {"*.dockerfile"})

    def test_no_partial_match(self):
        assert not _matches_any_pattern("not-requirements.txt", {"requirements.txt"})


# ---------------------------------------------------------------------------
# build_index
# ---------------------------------------------------------------------------

class TestBuildIndex:
    def test_build_index_empty_dir(self):
        root = tempfile.mkdtemp()
        try:
            index = build_index(root, {"requirements.txt"})
            assert index == {}
        finally:
            import shutil
            shutil.rmtree(root, ignore_errors=True)

    def test_build_index_finds_matching_files(self):
        root = _make_tmpdir_tree({
            "requirements.txt": "flask==2.0.0\n",
            "README.md": "# hello\n",
            "setup.py": "pass\n",
        })
        try:
            index = build_index(root, {"requirements.txt"})
            assert root in index
            assert "requirements.txt" in index[root]
            assert "README.md" not in index[root]
            assert "setup.py" not in index[root]
        finally:
            import shutil
            shutil.rmtree(root, ignore_errors=True)

    def test_build_index_skips_excluded_dirs(self):
        root = _make_tmpdir_tree({
            ".git": {"requirements.txt": ""},
            "node_modules": {"requirements.txt": ""},
            "__pycache__": {"requirements.txt": ""},
            ".venv": {"requirements.txt": ""},
            "src": {"requirements.txt": "real\n"},
        })
        try:
            index = build_index(root, {"requirements.txt"})
            # Only the src/ subdir file should appear
            found_paths = list(index.keys())
            for p in found_paths:
                basename = os.path.basename(p)
                assert basename not in {".git", "node_modules", "__pycache__", ".venv"}
            # src/requirements.txt should be found
            src_dir = os.path.join(root, "src")
            assert src_dir in index
            assert "requirements.txt" in index[src_dir]
        finally:
            import shutil
            shutil.rmtree(root, ignore_errors=True)

    def test_build_index_respects_max_depth(self):
        # depth 0 = root, depth 1 = a/, depth 2 = a/b/, depth 3 = a/b/c/
        root = _make_tmpdir_tree({
            "requirements.txt": "root\n",          # depth 0 — should be found (depth < max_depth=2)
            "a": {
                "requirements.txt": "level1\n",    # depth 1 — should be found
                "b": {
                    "requirements.txt": "level2\n",  # depth 2 — should NOT be found (depth >= max_depth=2)
                },
            },
        })
        try:
            index = build_index(root, {"requirements.txt"}, max_depth=2)
            all_files = []
            for dirpath, filenames in index.items():
                all_files.extend(os.path.join(dirpath, f) for f in filenames)

            rel_files = [os.path.relpath(p, root) for p in all_files]
            assert "requirements.txt" in rel_files
            assert os.path.join("a", "requirements.txt") in rel_files
            assert os.path.join("a", "b", "requirements.txt") not in rel_files
        finally:
            import shutil
            shutil.rmtree(root, ignore_errors=True)

    def test_build_index_respects_max_index_size(self, capsys):
        # Create 10 matching files, cap at 5
        files = {"file{}.txt".format(i): "content\n" for i in range(10)}
        root = _make_tmpdir_tree(files)
        try:
            patterns = {"file{}.txt".format(i) for i in range(10)}
            index = build_index(root, patterns, max_index_size=5)
            total = sum(len(fnames) for fnames in index.values())
            assert total == 5
            captured = capsys.readouterr()
            assert "index limit" in captured.err
        finally:
            import shutil
            shutil.rmtree(root, ignore_errors=True)

    def test_build_index_glob_patterns(self):
        root = _make_tmpdir_tree({
            "Dockerfile": "FROM ubuntu\n",
            "Dockerfile.prod": "FROM ubuntu\n",
            "Dockerfile.dev": "FROM ubuntu\n",
            "docker-compose.yml": "version: '3'\n",
        })
        try:
            index = build_index(root, {"Dockerfile*"})
            assert root in index
            assert "Dockerfile" in index[root]
            assert "Dockerfile.prod" in index[root]
            assert "Dockerfile.dev" in index[root]
            assert "docker-compose.yml" not in index[root]
        finally:
            import shutil
            shutil.rmtree(root, ignore_errors=True)

    def test_build_index_multiple_dirs(self):
        root = _make_tmpdir_tree({
            "requirements.txt": "root\n",
            "subdir": {
                "requirements.txt": "sub\n",
            },
        })
        try:
            index = build_index(root, {"requirements.txt"})
            assert root in index
            assert os.path.join(root, "subdir") in index
        finally:
            import shutil
            shutil.rmtree(root, ignore_errors=True)

    def test_build_index_default_max_depth_constant(self):
        assert DEFAULT_MAX_DEPTH == 4

    def test_build_index_default_max_index_size_constant(self):
        assert DEFAULT_MAX_INDEX_SIZE == 384


# ---------------------------------------------------------------------------
# run_checkers
# ---------------------------------------------------------------------------

class TestRunCheckers:
    def test_run_checkers_collects_findings(self):
        root = _make_tmpdir_tree({"dummy.txt": ""})
        try:
            index = build_index(root, {"dummy.txt"})
            checkers = [DummyChecker()]
            findings = run_checkers(checkers, index, root)
            assert len(findings) == 1
            assert findings[0].checker == "dummy"
        finally:
            import shutil
            shutil.rmtree(root, ignore_errors=True)

    def test_run_checkers_checker_crash(self):
        root = _make_tmpdir_tree({"crash.txt": ""})
        try:
            index = build_index(root, {"crash.txt"})
            checkers = [CrashingChecker()]
            findings = run_checkers(checkers, index, root)
            assert len(findings) == 1
            f = findings[0]
            assert f.checker == "crashing"
            assert "crashed" in f.message.lower() or "boom" in f.message
        finally:
            import shutil
            shutil.rmtree(root, ignore_errors=True)

    def test_run_checkers_empty_index(self):
        checkers = [DummyChecker()]
        findings = run_checkers(checkers, {}, "/tmp")
        assert findings == []

    def test_run_checkers_sorted_by_path_then_line(self):
        class MultiFindingChecker(Checker):
            name = "multi"
            description = "multiple findings"
            patterns = ["*.txt"]  # type: ignore[assignment]

            def check(self, index, root):
                return [
                    Finding(checker=self.name, path="z.txt", line=1, message="z"),
                    Finding(checker=self.name, path="a.txt", line=5, message="a5"),
                    Finding(checker=self.name, path="a.txt", line=2, message="a2"),
                ]

        checkers = [MultiFindingChecker()]
        findings = run_checkers(checkers, {"somedir": {"a.txt", "z.txt"}}, "/tmp")
        paths_lines = [(f.path, f.line) for f in findings]
        assert paths_lines == [("a.txt", 2), ("a.txt", 5), ("z.txt", 1)]


# ---------------------------------------------------------------------------
# format_text
# ---------------------------------------------------------------------------

class TestFormatText:
    def test_format_text_empty(self):
        output = format_text([])
        assert "0 findings" in output

    def test_format_text_with_findings(self):
        findings = [
            Finding(checker="test", path="req.txt", line=3, message="unpinned dep"),
            Finding(checker="test", path="setup.cfg", line=0, message="no pin"),
        ]
        output = format_text(findings)
        assert "FAIL" in output
        assert "req.txt:3" in output
        # line=0 => no colon+line number appended
        assert "setup.cfg:0" not in output
        assert "setup.cfg" in output
        assert "unpinned dep" in output
        assert "no pin" in output

    def test_format_text_fail_tags(self):
        findings = [
            Finding(checker="c", path="a.txt", line=1, message="err"),
            Finding(checker="c", path="b.txt", line=1, message="warn"),
        ]
        output = format_text(findings)
        lines = output.splitlines()
        fail_lines = [line for line in lines if line.startswith("FAIL")]
        assert len(fail_lines) == 2

    def test_format_text_error_counts(self):
        findings = [
            Finding(checker="c", path="a.txt", line=1, message="e1"),
            Finding(checker="c", path="b.txt", line=1, message="e2"),
            Finding(checker="c", path="c.txt", line=1, message="e3"),
        ]
        output = format_text(findings)
        assert "3 error" in output

    def test_format_text_single_error_no_plural(self):
        findings = [
            Finding(checker="c", path="a.txt", line=1, message="e"),
        ]
        output = format_text(findings)
        assert "1 error" in output
        assert "1 errors" not in output

    def test_format_text_summary_file_count(self):
        findings = [
            Finding(checker="c", path="a.txt", line=1, message="e1"),
            Finding(checker="c", path="a.txt", line=2, message="e2"),
            Finding(checker="c", path="b.txt", line=1, message="e3"),
        ]
        output = format_text(findings)
        # 2 unique files
        assert "2 files" in output

    def test_format_text_single_file_no_plural(self):
        findings = [
            Finding(checker="c", path="a.txt", line=1, message="w"),
        ]
        output = format_text(findings)
        assert "1 file" in output
        assert "1 files" not in output

    def test_format_text_line_zero_no_colon(self):
        findings = [
            Finding(checker="c", path="Makefile", line=0, message="no pin"),
        ]
        output = format_text(findings)
        # Should contain "Makefile" but NOT "Makefile:0"
        assert "Makefile" in output
        assert "Makefile:0" not in output


# ---------------------------------------------------------------------------
# validate_url_fragment_hashes
# ---------------------------------------------------------------------------

class TestValidateUrlFragmentHashes:
    def test_no_fragment(self):
        errors = validate_url_fragment_hashes("https://example.com/lib.tar.gz")
        assert len(errors) == 1
        assert "no hash fragment" in errors[0]

    def test_fragment_no_hash(self):
        errors = validate_url_fragment_hashes("https://example.com/lib.tar.gz#subdirectory=src")
        assert len(errors) == 1
        assert "no hash algorithm" in errors[0]

    def test_valid_sha256(self):
        url = "https://example.com/lib.tar.gz#sha256=" + "a" * 64
        errors = validate_url_fragment_hashes(url)
        assert errors == []

    def test_valid_sha512(self):
        url = "https://example.com/lib.tar.gz#sha512=" + "b" * 128
        errors = validate_url_fragment_hashes(url)
        assert errors == []

    def test_valid_sha1(self):
        url = "https://example.com/lib.tar.gz#sha1=" + "c" * 40
        errors = validate_url_fragment_hashes(url)
        assert errors == []

    def test_valid_sha224(self):
        url = "https://example.com/lib.tar.gz#sha224=" + "d" * 56
        errors = validate_url_fragment_hashes(url)
        assert errors == []

    def test_valid_sha384(self):
        url = "https://example.com/lib.tar.gz#sha384=" + "e" * 96
        errors = validate_url_fragment_hashes(url)
        assert errors == []

    def test_valid_md5(self):
        url = "https://example.com/lib.tar.gz#md5=" + "f" * 32
        errors = validate_url_fragment_hashes(url)
        assert errors == []

    def test_wrong_length_sha256(self):
        url = "https://example.com/lib.tar.gz#sha256=" + "a" * 32  # should be 64
        errors = validate_url_fragment_hashes(url)
        assert len(errors) == 1
        assert "wrong length" in errors[0]
        assert "64" in errors[0]

    def test_non_hex_characters(self):
        url = "https://example.com/lib.tar.gz#sha256=" + "g" * 64
        errors = validate_url_fragment_hashes(url)
        assert len(errors) == 1
        assert "non-hex" in errors[0]

    def test_empty_hash_value(self):
        url = "https://example.com/lib.tar.gz#sha256="
        errors = validate_url_fragment_hashes(url)
        assert len(errors) == 1
        assert "empty" in errors[0]

    def test_hash_after_subdirectory(self):
        url = "https://example.com/lib.tar.gz#subdirectory=src&sha256=" + "a" * 64
        errors = validate_url_fragment_hashes(url)
        assert errors == []

    def test_subdirectory_after_hash(self):
        url = "https://example.com/lib.tar.gz#sha256=" + "a" * 64 + "&subdirectory=src"
        errors = validate_url_fragment_hashes(url)
        assert errors == []

    def test_first_hash_valid_second_ignored(self):
        """pip uses only the first hash field. Second hash (even if different algo) is ignored."""
        url = "https://example.com/lib.tar.gz#sha256=" + "a" * 64 + "&sha512=" + "b" * 128
        errors = validate_url_fragment_hashes(url)
        assert errors == []

    def test_first_hash_valid_second_bad_still_passes(self):
        """First hash is valid — bad second hash is ignored (pip only checks first)."""
        url = "https://example.com/lib.tar.gz#sha256=" + "a" * 64 + "&sha512=bad"
        errors = validate_url_fragment_hashes(url)
        assert errors == []

    def test_first_hash_bad_second_valid_fails(self):
        """First hash is bad — valid second hash doesn't save it (pip uses first)."""
        url = "https://example.com/lib.tar.gz#sha512=bad&sha256=" + "a" * 64
        errors = validate_url_fragment_hashes(url)
        assert len(errors) == 1
        assert "sha512" in errors[0]

    def test_case_insensitive_algorithm(self):
        url = "https://example.com/lib.tar.gz#SHA256=" + "a" * 64
        errors = validate_url_fragment_hashes(url)
        assert errors == []

    def test_duplicate_first_valid_second_bad(self):
        """Same algorithm twice — first is valid, second ignored (pip uses first per algo)."""
        url = "https://example.com/lib.tar.gz#sha256=" + "a" * 64 + "&sha256=bad"
        errors = validate_url_fragment_hashes(url)
        assert errors == []

    def test_duplicate_first_bad_second_valid(self):
        """Same algorithm twice — first is bad, valid second ignored (pip uses first)."""
        url = "https://example.com/lib.tar.gz#sha256=xyz&sha256=" + "a" * 64
        errors = validate_url_fragment_hashes(url)
        assert len(errors) == 1
        assert "sha256" in errors[0]
