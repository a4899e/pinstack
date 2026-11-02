"""Tests for the Cargo checker."""

import os

from pinstack.checkers.cargo import CargoChecker

CARGO_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "cargo")


def _check(subpath):
    # type: (str) -> list
    dirpath = os.path.join(CARGO_FIXTURES, subpath)
    index = {dirpath: {"Cargo.lock"}}
    return CargoChecker().check(index, dirpath)


def _check_empty():
    # type: () -> list
    return CargoChecker().check({}, "/tmp")


class TestCargoGood:
    def test_all_checksums_no_findings(self):
        findings = _check("good")
        assert findings == [], (
            "All registry packages with checksum should produce 0 findings, got: {}".format(
                [f.message for f in findings]
            )
        )

    def test_empty_index_no_findings(self):
        assert _check_empty() == []


class TestCargoBad:
    def setup_method(self):
        self.findings = _check("bad")

    def test_one_finding(self):
        assert len(self.findings) == 1, (
            "Expected 1 finding for tokio missing checksum, got {}: {}".format(
                len(self.findings), [f.message for f in self.findings]
            )
        )

    def test_has_one_finding(self):
        assert len(self.findings) == 1

    def test_tokio_flagged(self):
        assert "tokio" in self.findings[0].message

    def test_checker_name(self):
        assert self.findings[0].checker == "cargo"

    def test_path_is_relative(self):
        assert not os.path.isabs(self.findings[0].path)

    def test_line_number_positive(self):
        assert self.findings[0].line > 0


class TestCargoLocalCrate:
    def test_local_crate_no_source_skipped(self):
        """Local crates (no source =) should not produce findings."""
        findings = _check("local")
        assert findings == [], (
            "Local path crates should not be flagged, got: {}".format(
                [f.message for f in findings]
            )
        )


class TestCargoGitSource:
    def test_git_source_no_false_positive(self):
        """Git-sourced packages legitimately have no checksum and must not be flagged."""
        findings = _check("git_source")
        assert findings == [], (
            "Git-sourced package without checksum should produce 0 findings, got: {}".format(
                [f.message for f in findings]
            )
        )


class TestCargoNoLock:
    def test_no_cargo_lock_no_findings(self):
        assert _check_empty() == []


def _scan(subpath):
    # type: (str) -> list
    """Index a fixture tree the way the CLI does and run the checker over it."""
    from pinstack.core import build_index

    dirpath = os.path.join(CARGO_FIXTURES, subpath)
    index = build_index(dirpath, set(CargoChecker.patterns))
    return CargoChecker().check(index, dirpath)


class TestCargoMissingLock:
    """Review repro M3: Cargo.toml with dependencies and no Cargo.lock."""

    def test_manifest_without_lock_flagged(self):
        findings = _scan("no_lock")
        assert len(findings) == 1
        assert findings[0].path == "Cargo.toml"
        assert "no Cargo.lock" in findings[0].message
        assert findings[0].integrity

    def test_manifest_without_deps_not_flagged(self):
        """Nothing to resolve, so nothing to lock."""
        assert _scan("no_deps_no_lock") == []

    def test_workspace_member_uses_root_lock(self):
        """A member's Cargo.toml is locked by the workspace root's Cargo.lock."""
        assert _scan("workspace") == []

    def test_member_scanned_alone_flagged(self):
        """The ancestor search stops at the scan root."""
        findings = _scan(os.path.join("workspace", "crates", "member"))
        assert [f.path for f in findings] == ["Cargo.toml"]


def _scan_tree(tree):
    # type: (dict) -> list
    """Write {relpath: content} under a temp dir, index it and run the checker."""
    import shutil
    import tempfile

    from pinstack.core import build_index

    d = tempfile.mkdtemp()
    try:
        for rel, content in tree.items():
            path = os.path.join(d, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as fh:
                fh.write(content)
        index = build_index(d, set(CargoChecker.patterns))
        return [
            (f.path.replace(os.sep, "/"), f.message)
            for f in CargoChecker().check(index, d)
        ]
    finally:
        shutil.rmtree(d, ignore_errors=True)


_LOCK_SERDE = '[[package]]\nname = "serde"\nversion = "1.0.188"\n'


class TestCargoWorkspaceMembership:
    def test_nested_non_member_crate_needs_own_lock(self):
        """A parent's Cargo.lock only covers workspace members."""
        findings = _scan_tree(
            {
                "Cargo.toml": '[package]\nname = "app"\n[dependencies]\nserde = "1"\n',
                "Cargo.lock": _LOCK_SERDE + '\n[[package]]\nname = "app"\n',
                "tools/x/Cargo.toml": '[package]\nname = "x"\n[dependencies]\nrand = "0.8"\n',
            }
        )
        assert [p for p, _ in findings] == ["tools/x/Cargo.toml"]
        assert "no Cargo.lock" in findings[0][1]

    def test_excluded_crate_needs_own_lock(self):
        findings = _scan_tree(
            {
                "Cargo.toml": '[workspace]\nmembers = ["crates/*"]\nexclude = ["crates/skip"]\n',
                "Cargo.lock": _LOCK_SERDE,
                "crates/skip/Cargo.toml": '[package]\nname = "skip"\n[dependencies]\nserde = "1"\n',
            }
        )
        assert [p for p, _ in findings] == ["crates/skip/Cargo.toml"]

    def test_member_glob_does_not_cross_directories(self):
        """`crates/*` matches crates/a, not crates/a/b."""
        findings = _scan_tree(
            {
                "Cargo.toml": '[workspace]\nmembers = [\n  "crates/*",\n]\n',
                "Cargo.lock": _LOCK_SERDE,
                "crates/a/b/Cargo.toml": '[package]\nname = "b"\n[dependencies]\nserde = "1"\n',
            }
        )
        assert [p for p, _ in findings] == ["crates/a/b/Cargo.toml"]

    def test_root_path_dependency_is_member(self):
        findings = _scan_tree(
            {
                "Cargo.toml": '[package]\nname = "app"\n[workspace]\n[dependencies]\nlib = { path = "lib" }\n',
                "Cargo.lock": _LOCK_SERDE
                + '\n[[package]]\nname = "lib"\n\n[[package]]\nname = "app"\n',
                "lib/Cargo.toml": '[package]\nname = "lib"\n[dependencies]\nserde = "1"\n',
            }
        )
        assert findings == []

    def test_member_dependency_missing_from_workspace_lock(self):
        findings = _scan_tree(
            {
                "Cargo.toml": '[workspace]\nmembers = ["m"]\n',
                "Cargo.lock": _LOCK_SERDE,
                "m/Cargo.toml": '[package]\nname = "m"\n[dependencies]\nserde = "1"\nrand = "0.8"\n',
            }
        )
        assert findings == [
            ("m/Cargo.toml", "Cargo.lock is stale: missing 1 dependency (rand)")
        ]

    def test_workspace_root_without_lock(self):
        findings = _scan_tree(
            {
                "Cargo.toml": '[workspace]\nmembers = ["m"]\n',
                "m/Cargo.toml": '[package]\nname = "m"\n[dependencies]\nserde = "1"\n',
            }
        )
        assert [p for p, _ in findings] == ["m/Cargo.toml"]


class TestCargoTomlDependencyForms:
    def test_dependency_table_form(self):
        """[dependencies.serde] declares one dependency."""
        findings = _scan_tree(
            {
                "Cargo.toml": '[package]\nname = "x"\n[dependencies.serde]\nversion = "1"\n'
            }
        )
        assert [p for p, _ in findings] == ["Cargo.toml"]

    def test_dotted_workspace_key(self):
        """`serde.workspace = true` declares serde."""
        from pinstack.checkers.cargo import _parse_cargo_toml_deps
        import tempfile

        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "Cargo.toml")
            with open(path, "w") as fh:
                fh.write(
                    "[dependencies]\nserde.workspace = true\n"
                    "[target.'cfg(unix)'.dependencies.libc]\nversion = \"0.2\"\n"
                )
            assert _parse_cargo_toml_deps(path) == ["serde", "libc"]
        finally:
            import shutil

            shutil.rmtree(d, ignore_errors=True)


class TestCargoManifestSyntax:
    """Codex review of #13: legal TOML must not read as 'no dependencies'."""

    def test_commented_section_header(self):
        findings = _scan_tree(
            {
                "Cargo.toml": '[package]\nname = "x"\n[dependencies] # registry crates\nserde = "1"\n'
            }
        )
        assert [p for p, _ in findings] == ["Cargo.toml"]

    def test_quoted_dependency_key(self):
        findings = _scan_tree(
            {"Cargo.toml": '[package]\nname = "x"\n[dependencies]\n"serde" = "1"\n'}
        )
        assert [p for p, _ in findings] == ["Cargo.toml"]

    def test_commented_workspace_header_and_literal_strings(self):
        findings = _scan_tree(
            {
                "Cargo.toml": "[workspace] # shared lock\nmembers = ['crates/a']\n",
                "Cargo.lock": _LOCK_SERDE,
                "crates/a/Cargo.toml": '[package]\nname = "a"\n[dependencies]\nserde = "1"\n',
            }
        )
        assert findings == []

    def test_member_path_dependency_is_a_member(self):
        """Cargo adds path dependencies of members inside the workspace dir."""
        findings = _scan_tree(
            {
                "Cargo.toml": '[workspace]\nmembers = ["crates/app"]\n',
                "Cargo.lock": _LOCK_SERDE
                + '\n[[package]]\nname = "app"\n\n[[package]]\nname = "helper"\n',
                "crates/app/Cargo.toml": '[package]\nname = "app"\n[dependencies]\n'
                'helper = { path = "../../shared/helper" }\n',
                "shared/helper/Cargo.toml": '[package]\nname = "helper"\n[dependencies]\nserde = "1"\n',
            }
        )
        assert findings == []

    def test_renamed_dependency_uses_package_name(self):
        findings = _scan_tree(
            {
                "Cargo.toml": '[workspace]\nmembers = ["m"]\n',
                "Cargo.lock": _LOCK_SERDE,
                "m/Cargo.toml": '[package]\nname = "m"\n[dependencies]\n'
                'serde1 = { package = "serde", version = "1" }\n',
            }
        )
        assert findings == []

    def test_inherited_renamed_dependency(self):
        findings = _scan_tree(
            {
                "Cargo.toml": '[workspace]\nmembers = ["m"]\n'
                '[workspace.dependencies]\nserde1 = { package = "serde", version = "1" }\n',
                "Cargo.lock": _LOCK_SERDE,
                "m/Cargo.toml": '[package]\nname = "m"\n[dependencies]\nserde1.workspace = true\n',
            }
        )
        assert findings == []

    def test_multiline_members_array_with_comments(self):
        findings = _scan_tree(
            {
                "Cargo.toml": "[workspace]\nmembers = [\n  \"a\",  # first\n  'b',\n]\n",
                "Cargo.lock": _LOCK_SERDE,
                "a/Cargo.toml": '[package]\nname = "a"\n[dependencies]\nserde = "1"\n',
                "b/Cargo.toml": '[package]\nname = "b"\n[dependencies]\nserde = "1"\n',
            }
        )
        assert findings == []

    def test_dotted_rename_is_one_dependency(self):
        """Codex #13 follow-up: dotted fields describe one renamed dependency."""
        findings = _scan_tree(
            {
                "Cargo.toml": '[workspace]\nmembers = ["m"]\n',
                "Cargo.lock": _LOCK_SERDE,
                "m/Cargo.toml": '[package]\nname = "m"\n[dependencies]\n'
                'serde1.package = "serde"\nserde1.version = "1"\n',
            }
        )
        assert findings == []

    def test_dotted_workspace_rename_keeps_package(self):
        """A later dotted field must not drop an earlier `package` in [workspace.dependencies]."""
        findings = _scan_tree(
            {
                "Cargo.toml": '[workspace]\nmembers = ["m"]\n'
                '[workspace.dependencies]\nserde1.package = "serde"\nserde1.version = "1"\n',
                "Cargo.lock": _LOCK_SERDE,
                "m/Cargo.toml": '[package]\nname = "m"\n[dependencies]\nserde1.workspace = true\n',
            }
        )
        assert findings == []

    def test_inherited_path_dependency_is_a_member(self):
        """Codex #13 follow-up: a `workspace = true` path dep resolves against the root."""
        findings = _scan_tree(
            {
                "Cargo.toml": '[workspace]\nmembers = ["crates/app"]\n'
                '[workspace.dependencies]\nhelper = { path = "shared/helper" }\n',
                "Cargo.lock": _LOCK_SERDE
                + '\n[[package]]\nname = "app"\n\n[[package]]\nname = "helper"\n',
                "crates/app/Cargo.toml": '[package]\nname = "app"\n[dependencies]\n'
                "helper.workspace = true\n",
                "shared/helper/Cargo.toml": '[package]\nname = "helper"\n[dependencies]\nserde = "1"\n',
            }
        )
        assert findings == []
