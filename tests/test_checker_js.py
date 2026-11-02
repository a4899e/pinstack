"""Tests for the four JS ecosystem checkers: package_json, package_lock, yarn_lock, pnpm_lock."""

import json
import os
import shutil
import tempfile

from pinstack.checkers.package_json import PackageJsonChecker
from pinstack.checkers.package_lock import PackageLockChecker
from pinstack.checkers.yarn_lock import YarnLockChecker
from pinstack.checkers.pnpm_lock import PnpmLockChecker

JS_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "js")


def _check_fixture_dir(checker, subpath, extra_files=None):
    # type: (object, str, set) -> list
    """Run a checker against a single fixture directory, rooted at that directory."""
    dirpath = os.path.join(JS_FIXTURES, subpath)
    fname = checker.patterns[0]
    files = {fname}
    if extra_files:
        files = files | extra_files
    index = {dirpath: files}
    return checker.check(index, dirpath)


def _check_empty(checker):
    # type: (object, ) -> list
    return checker.check({}, "/tmp")


# ---------------------------------------------------------------------------
# package.json
# ---------------------------------------------------------------------------


class TestPackageJsonGood:
    def test_exact_versions_no_findings(self):
        findings = _check_fixture_dir(
            PackageJsonChecker(), "package_json/good", extra_files={"package-lock.json"}
        )
        assert findings == [], "Exact versions should produce 0 findings"

    def test_no_dependencies_no_findings(self):
        findings = _check_fixture_dir(PackageJsonChecker(), "package_json/no_deps")
        assert findings == [], (
            "package.json with no dep sections should produce 0 findings"
        )

    def test_workspace_protocol_not_flagged(self):
        findings = _check_fixture_dir(PackageJsonChecker(), "package_json/workspace")
        msgs = [f.message for f in findings]
        assert not any("workspace" in m for m in msgs), (
            "workspace:* should not be flagged"
        )
        assert not any("shared-lib" in m for m in msgs)

    def test_file_protocol_not_flagged(self):
        findings = _check_fixture_dir(PackageJsonChecker(), "package_json/workspace")
        msgs = [f.message for f in findings]
        assert not any("local-utils" in m for m in msgs), (
            "file:../utils should not be flagged"
        )

    def test_url_version_flagged_as_not_content_addressed(self):
        findings = _check_fixture_dir(PackageJsonChecker(), "package_json/workspace")
        url_findings = [f for f in findings if "remote-pkg" in f.message]
        assert len(url_findings) == 1, (
            "https:// URL should be flagged as not content-addressed"
        )
        assert "not content-addressed" in url_findings[0].message

    def test_empty_index_no_findings(self):
        assert _check_empty(PackageJsonChecker()) == []


class TestPackageJsonBad:
    def setup_method(self):
        self.findings = _check_fixture_dir(PackageJsonChecker(), "package_json/bad")

    def test_has_four_findings(self):
        # 4 pinning errors + 1 lock-file companion warning = 5 total
        assert len(self.findings) == 5, "Expected 5 findings, got {}: {}".format(
            len(self.findings), [f.message for f in self.findings]
        )

    def test_caret_express_flagged(self):
        msgs = [f.message for f in self.findings]
        assert any("express" in m for m in msgs), "express ^4.18.2 should be flagged"

    def test_tilde_lodash_flagged(self):
        msgs = [f.message for f in self.findings]
        assert any("lodash" in m for m in msgs), "lodash ~4.17.21 should be flagged"

    def test_gte_jest_flagged(self):
        msgs = [f.message for f in self.findings]
        assert any("jest" in m for m in msgs), "jest >=29.0.0 should be flagged"

    def test_caret_react_flagged(self):
        msgs = [f.message for f in self.findings]
        assert any("react" in m for m in msgs), "react ^18.0.0 should be flagged"

    def test_all_findings_count(self):
        # 4 pinning errors + 1 missing lock file finding = 5
        assert len(self.findings) == 5

    def test_findings_line_zero(self):
        for f in self.findings:
            assert f.line == 0, "JSON findings should have line=0"

    def test_checker_name(self):
        for f in self.findings:
            assert f.checker == "package_json"

    def test_paths_are_relative(self):
        for f in self.findings:
            assert not os.path.isabs(f.path)


# ---------------------------------------------------------------------------
# package-lock.json
# ---------------------------------------------------------------------------


class TestPackageLockGood:
    def test_all_integrity_no_findings(self):
        findings = _check_fixture_dir(PackageLockChecker(), "package_lock/good")
        assert findings == [], "All packages with integrity should produce 0 findings"

    def test_empty_index_no_findings(self):
        assert _check_empty(PackageLockChecker()) == []

    def test_root_entry_skipped(self):
        # good fixture has root "" entry with no integrity — should not produce a finding
        findings = _check_fixture_dir(PackageLockChecker(), "package_lock/good")
        assert findings == []

    def test_link_packages_skipped(self):
        findings = _check_fixture_dir(PackageLockChecker(), "package_lock/link")
        assert findings == [], (
            "link:true packages without integrity should not be flagged"
        )


class TestPackageLockBad:
    def setup_method(self):
        self.findings = _check_fixture_dir(PackageLockChecker(), "package_lock/bad")

    def test_one_finding(self):
        assert len(self.findings) == 1, (
            "Expected 1 finding for lodash missing integrity, got {}: {}".format(
                len(self.findings), [f.message for f in self.findings]
            )
        )

    def test_finding_message_contains_package(self):
        assert "lodash" in self.findings[0].message

    def test_checker_name(self):
        assert self.findings[0].checker == "package_lock"

    def test_path_is_relative(self):
        assert not os.path.isabs(self.findings[0].path)


class TestPackageLockV1Good:
    def test_lockfile_v1_good(self):
        """v1 lockfile with integrity on all deps should produce 0 findings."""
        findings = _check_fixture_dir(PackageLockChecker(), "package_lock/v1_good")
        assert findings == [], (
            "v1 lockfile with all integrity hashes should produce 0 findings, got: {}".format(
                [f.message for f in findings]
            )
        )


class TestPackageLockV1Bad:
    def setup_method(self):
        self.findings = _check_fixture_dir(PackageLockChecker(), "package_lock/v1_bad")

    def test_lockfile_v1_bad(self):
        """v1 lockfile with dep missing integrity should produce 1 finding."""
        assert len(self.findings) == 1, (
            "Expected 1 finding for lodash missing integrity, got {}: {}".format(
                len(self.findings), [f.message for f in self.findings]
            )
        )

    def test_finding_message_contains_package(self):
        assert "lodash" in self.findings[0].message

    def test_checker_name(self):
        assert self.findings[0].checker == "package_lock"

    def test_path_is_relative(self):
        assert not os.path.isabs(self.findings[0].path)


# ---------------------------------------------------------------------------
# yarn.lock
# ---------------------------------------------------------------------------


class TestYarnLockGood:
    def test_all_integrity_no_findings(self):
        findings = _check_fixture_dir(YarnLockChecker(), "yarn_lock/good")
        assert findings == [], "All entries with integrity should produce 0 findings"

    def test_empty_index_no_findings(self):
        assert _check_empty(YarnLockChecker()) == []


class TestYarnBerryChecksum:
    def test_yarn_berry_checksum(self):
        """Yarn Berry lockfiles using checksum: instead of integrity should produce 0 findings."""
        import tempfile

        content = (
            "__metadata:\n"
            "  version: 6\n"
            "  cacheKey: 8\n"
            "\n"
            '"express@npm:4.18.2":\n'
            "  version: 4.18.2\n"
            '  resolution: "express@npm:4.18.2"\n'
            "  checksum: 10/abc123def456\n"
            "\n"
            '"lodash@npm:4.17.21":\n'
            "  version: 4.17.21\n"
            '  resolution: "lodash@npm:4.17.21"\n'
            "  checksum: 10/789xyz\n"
        )
        tmpdir = tempfile.mkdtemp()
        try:
            lock_path = os.path.join(tmpdir, "yarn.lock")
            with open(lock_path, "w") as fh:
                fh.write(content)
            index = {tmpdir: {"yarn.lock"}}
            findings = YarnLockChecker().check(index, tmpdir)
            assert findings == [], (
                "Yarn Berry checksum: should be accepted, got: {}".format(
                    [f.message for f in findings]
                )
            )
        finally:
            import shutil

            shutil.rmtree(tmpdir, ignore_errors=True)


class TestYarnLockBad:
    def setup_method(self):
        self.findings = _check_fixture_dir(YarnLockChecker(), "yarn_lock/bad")

    def test_one_finding(self):
        assert len(self.findings) == 1, (
            "Expected 1 finding for lodash missing integrity, got {}: {}".format(
                len(self.findings), [f.message for f in self.findings]
            )
        )

    def test_finding_message_contains_package(self):
        assert "lodash" in self.findings[0].message

    def test_checker_name(self):
        assert self.findings[0].checker == "yarn_lock"

    def test_path_is_relative(self):
        assert not os.path.isabs(self.findings[0].path)

    def test_finding_has_line_number(self):
        assert self.findings[0].line > 0


# ---------------------------------------------------------------------------
# pnpm-lock.yaml
# ---------------------------------------------------------------------------


class TestPnpmLockGood:
    def test_all_integrity_no_findings(self):
        findings = _check_fixture_dir(PnpmLockChecker(), "pnpm_lock/good")
        assert findings == [], "All packages with integrity should produce 0 findings"

    def test_empty_index_no_findings(self):
        assert _check_empty(PnpmLockChecker()) == []


class TestPnpmLockBad:
    def setup_method(self):
        self.findings = _check_fixture_dir(PnpmLockChecker(), "pnpm_lock/bad")

    def test_one_finding(self):
        assert len(self.findings) == 1, (
            "Expected 1 finding for lodash missing integrity, got {}: {}".format(
                len(self.findings), [f.message for f in self.findings]
            )
        )

    def test_finding_message_contains_package(self):
        assert "lodash" in self.findings[0].message

    def test_checker_name(self):
        assert self.findings[0].checker == "pnpm_lock"

    def test_path_is_relative(self):
        assert not os.path.isabs(self.findings[0].path)

    def test_finding_has_line_number(self):
        assert self.findings[0].line > 0


# ---------------------------------------------------------------------------
# package.json lock file companion check tests
# ---------------------------------------------------------------------------

_PACKAGE_JSON_WITH_DEPS = json.dumps(
    {
        "name": "my-app",
        "version": "1.0.0",
        "dependencies": {"express": "4.18.2"},
    }
)

_PACKAGE_JSON_NO_DEPS = json.dumps(
    {
        "name": "my-app",
        "version": "1.0.0",
    }
)


def _make_package_json(tmpdir, content):
    # type: (str, str) -> None
    path = os.path.join(tmpdir, "package.json")
    with open(path, "w") as fh:
        fh.write(content)


class TestPackageJsonLockFileCheck:
    def test_no_lock_file_warns(self):
        tmpdir = tempfile.mkdtemp()
        try:
            _make_package_json(tmpdir, _PACKAGE_JSON_WITH_DEPS)
            checker = PackageJsonChecker()
            index = {tmpdir: {"package.json"}}
            findings = checker.check(index, tmpdir)
            lock_warnings = [f for f in findings if "lock file" in f.message]
            assert len(lock_warnings) == 1
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_package_lock_satisfies(self):
        tmpdir = tempfile.mkdtemp()
        try:
            _make_package_json(tmpdir, _PACKAGE_JSON_WITH_DEPS)
            checker = PackageJsonChecker()
            index = {tmpdir: {"package.json", "package-lock.json"}}
            findings = checker.check(index, tmpdir)
            lock_warnings = [f for f in findings if "lock file" in f.message]
            assert lock_warnings == []
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_yarn_lock_satisfies(self):
        tmpdir = tempfile.mkdtemp()
        try:
            _make_package_json(tmpdir, _PACKAGE_JSON_WITH_DEPS)
            checker = PackageJsonChecker()
            index = {tmpdir: {"package.json", "yarn.lock"}}
            findings = checker.check(index, tmpdir)
            lock_warnings = [f for f in findings if "lock file" in f.message]
            assert lock_warnings == []
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_pnpm_lock_satisfies(self):
        tmpdir = tempfile.mkdtemp()
        try:
            _make_package_json(tmpdir, _PACKAGE_JSON_WITH_DEPS)
            checker = PackageJsonChecker()
            index = {tmpdir: {"package.json", "pnpm-lock.yaml"}}
            findings = checker.check(index, tmpdir)
            lock_warnings = [f for f in findings if "lock file" in f.message]
            assert lock_warnings == []
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_no_deps_no_warning(self):
        tmpdir = tempfile.mkdtemp()
        try:
            _make_package_json(tmpdir, _PACKAGE_JSON_NO_DEPS)
            checker = PackageJsonChecker()
            index = {tmpdir: {"package.json"}}
            findings = checker.check(index, tmpdir)
            assert findings == []
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_lock_file_finding_present(self):
        tmpdir = tempfile.mkdtemp()
        try:
            _make_package_json(tmpdir, _PACKAGE_JSON_WITH_DEPS)
            checker = PackageJsonChecker()
            index = {tmpdir: {"package.json"}}
            findings = checker.check(index, tmpdir)
            lock_findings = [f for f in findings if "lock file" in f.message]
            assert len(lock_findings) == 1
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# package.json lock file cross-reference tests
# ---------------------------------------------------------------------------

_PACKAGE_JSON_TWO_DEPS = json.dumps(
    {
        "name": "my-app",
        "version": "1.0.0",
        "dependencies": {"express": "4.18.2", "lodash": "4.17.21"},
    }
)


class TestPackageJsonLockFileCrossRef:
    def test_dep_missing_from_package_lock(self):
        tmpdir = tempfile.mkdtemp()
        try:
            _make_package_json(tmpdir, _PACKAGE_JSON_TWO_DEPS)
            lock_path = os.path.join(tmpdir, "package-lock.json")
            lock_data = {
                "name": "my-app",
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "my-app", "version": "1.0.0"},
                    "node_modules/express": {
                        "version": "4.18.2",
                        "integrity": "sha512-abc",
                    },
                },
            }
            with open(lock_path, "w") as fh:
                json.dump(lock_data, fh)
            checker = PackageJsonChecker()
            index = {tmpdir: {"package.json", "package-lock.json"}}
            findings = checker.check(index, tmpdir)
            cross_ref = [f for f in findings if "stale" in f.message]
            assert len(cross_ref) == 1
            assert "lodash" in cross_ref[0].message
            assert "package-lock.json" in cross_ref[0].message
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_dep_present_in_package_lock(self):
        tmpdir = tempfile.mkdtemp()
        try:
            _make_package_json(tmpdir, _PACKAGE_JSON_TWO_DEPS)
            lock_path = os.path.join(tmpdir, "package-lock.json")
            lock_data = {
                "name": "my-app",
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "my-app", "version": "1.0.0"},
                    "node_modules/express": {
                        "version": "4.18.2",
                        "integrity": "sha512-abc",
                    },
                    "node_modules/lodash": {
                        "version": "4.17.21",
                        "integrity": "sha512-def",
                    },
                },
            }
            with open(lock_path, "w") as fh:
                json.dump(lock_data, fh)
            checker = PackageJsonChecker()
            index = {tmpdir: {"package.json", "package-lock.json"}}
            findings = checker.check(index, tmpdir)
            cross_ref = [f for f in findings if "stale" in f.message]
            assert cross_ref == []
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_dep_missing_from_yarn_lock(self):
        tmpdir = tempfile.mkdtemp()
        try:
            _make_package_json(tmpdir, _PACKAGE_JSON_TWO_DEPS)
            lock_path = os.path.join(tmpdir, "yarn.lock")
            with open(lock_path, "w") as fh:
                fh.write(
                    "# yarn lockfile v1\n"
                    "\n"
                    "express@4.18.2:\n"
                    '  version "4.18.2"\n'
                    "  integrity sha512-abc\n"
                )
            checker = PackageJsonChecker()
            index = {tmpdir: {"package.json", "yarn.lock"}}
            findings = checker.check(index, tmpdir)
            cross_ref = [f for f in findings if "stale" in f.message]
            assert len(cross_ref) == 1
            assert "lodash" in cross_ref[0].message
            assert "yarn.lock" in cross_ref[0].message
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_dep_missing_from_pnpm_lock(self):
        tmpdir = tempfile.mkdtemp()
        try:
            _make_package_json(tmpdir, _PACKAGE_JSON_TWO_DEPS)
            lock_path = os.path.join(tmpdir, "pnpm-lock.yaml")
            with open(lock_path, "w") as fh:
                fh.write(
                    "lockfileVersion: 5\n"
                    "\n"
                    "packages:\n"
                    "  /express@4.18.2:\n"
                    "    resolution: {integrity: sha512-abc}\n"
                )
            checker = PackageJsonChecker()
            index = {tmpdir: {"package.json", "pnpm-lock.yaml"}}
            findings = checker.check(index, tmpdir)
            cross_ref = [f for f in findings if "stale" in f.message]
            assert len(cross_ref) == 1
            assert "lodash" in cross_ref[0].message
            assert "pnpm-lock.yaml" in cross_ref[0].message
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Lock files that parse to no packages (fail closed)
# ---------------------------------------------------------------------------


def _run_with_lock(lock_name, lock_content, deps=None):
    # type: (str, str, dict) -> list
    d = tempfile.mkdtemp()
    try:
        with open(os.path.join(d, "package.json"), "w") as fh:
            json.dump({"dependencies": deps or {"left-pad": "1.3.0"}}, fh)
        with open(os.path.join(d, lock_name), "w") as fh:
            fh.write(lock_content)
        return PackageJsonChecker().check({d: {"package.json", lock_name}}, d)
    finally:
        shutil.rmtree(d, ignore_errors=True)


class TestPackageJsonEmptyLock:
    def test_empty_object_package_lock(self):
        """Review repro M1: package-lock.json containing {} satisfied the gate."""
        findings = _run_with_lock("package-lock.json", "{}")
        by_path = {f.path: f.message for f in findings}
        assert by_path["package-lock.json"] == (
            "package-lock.json contains no packages (empty or unrecognised format)"
        )
        assert "missing 1 dependency (left-pad)" in by_path["package.json"]
        assert all(f.integrity for f in findings)

    def test_unparseable_package_lock(self):
        findings = _run_with_lock("package-lock.json", "not json")
        assert any("contains no packages" in f.message for f in findings)

    def test_zero_byte_yarn_lock(self):
        findings = _run_with_lock("yarn.lock", "")
        assert any(f.path == "yarn.lock" for f in findings)

    def test_lockfile_v1_dependencies_map(self):
        """lockfileVersion 1 lists packages under "dependencies", not "packages"."""
        lock = json.dumps(
            {
                "lockfileVersion": 1,
                "dependencies": {"left-pad": {"version": "1.3.0", "integrity": "x"}},
            }
        )
        assert _run_with_lock("package-lock.json", lock) == []

    def test_nested_node_modules_key(self):
        lock = json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {},
                    "node_modules/a/node_modules/@s/left-pad": {"version": "1.3.0"},
                },
            }
        )
        assert _run_with_lock("package-lock.json", lock, {"@s/left-pad": "1.3.0"}) == []

    def test_pnpm_v9_keys_without_slash(self):
        lock = (
            "lockfileVersion: '9.0'\n"
            "importers:\n"
            "  .:\n"
            "    dependencies:\n"
            "      left-pad:\n"
            "        specifier: 1.3.0\n"
            "        version: 1.3.0\n"
            "packages:\n"
            "  left-pad@1.3.0:\n"
            "    resolution: {integrity: sha512-x}\n"
            "  '@s/p@1.0.0':\n"
            "    resolution: {integrity: sha512-y}\n"
            "snapshots:\n"
            "  left-pad@1.3.0: {}\n"
        )
        findings = _run_with_lock(
            "pnpm-lock.yaml", lock, {"left-pad": "1.3.0", "@s/p": "1.0.0"}
        )
        assert findings == []

    def test_pnpm_v6_peer_suffix_and_v5_slash_version(self):
        lock = (
            "lockfileVersion: 5.4\n"
            "packages:\n"
            "  /left-pad/1.3.0:\n"
            "    resolution: {integrity: sha512-x}\n"
            "  /@s/p@1.0.0(react@18.2.0):\n"
            "    resolution: {integrity: sha512-y}\n"
        )
        findings = _run_with_lock(
            "pnpm-lock.yaml", lock, {"left-pad": "1.3.0", "@s/p": "1.0.0"}
        )
        assert findings == []

    def test_pnpm_lock_without_packages_section(self):
        findings = _run_with_lock("pnpm-lock.yaml", "lockfileVersion: '9.0'\n")
        assert any(f.path == "pnpm-lock.yaml" for f in findings)

    def test_pnpm_real_layout_with_blank_lines(self):
        """pnpm v6-v9 separate sections and entries with blank lines."""
        lock = (
            "lockfileVersion: '9.0'\n"
            "\n"
            "settings:\n"
            "  autoInstallPeers: true\n"
            "\n"
            "importers:\n"
            "\n"
            "  .:\n"
            "    dependencies:\n"
            "      express:\n"
            "        specifier: 4.18.2\n"
            "        version: 4.18.2\n"
            "\n"
            "packages:\n"
            "\n"
            "  accepts@1.3.8:\n"
            "    resolution: {integrity: sha512-x}\n"
            "\n"
            "  express@4.18.2:\n"
            "    resolution: {integrity: sha512-y}\n"
            "\n"
            "snapshots:\n"
            "\n"
            "  express@4.18.2: {}\n"
        )
        assert _run_with_lock("pnpm-lock.yaml", lock, {"express": "4.18.2"}) == []

    def test_pnpm_v5_peer_suffix(self):
        lock = (
            "lockfileVersion: 5.4\n"
            "\n"
            "packages:\n"
            "\n"
            "  /react-dom/18.2.0_react@18.2.0:\n"
            "    resolution: {integrity: sha512-x}\n"
            "\n"
            "  /@s/p/1.0.0_react@18.2.0:\n"
            "    resolution: {integrity: sha512-y}\n"
            "\n"
            "  /7zip-bin/5.1.1:\n"
            "    resolution: {integrity: sha512-z}\n"
        )
        deps = {"react-dom": "18.2.0", "@s/p": "1.0.0", "7zip-bin": "5.1.1"}
        assert _run_with_lock("pnpm-lock.yaml", lock, deps) == []

    def test_pnpm_v6_name_starting_with_digit(self):
        lock = "lockfileVersion: '6.0'\npackages:\n  /7zip-bin@5.1.1:\n    x: y\n"
        assert _run_with_lock("pnpm-lock.yaml", lock, {"7zip-bin": "5.1.1"}) == []


class TestPackageJsonMultipleLocks:
    def test_empty_lock_beside_valid_lock(self):
        """The valid lock still satisfies the cross-reference; the empty one is reported."""
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, "package.json"), "w") as fh:
                json.dump({"dependencies": {"left-pad": "1.3.0"}}, fh)
            with open(os.path.join(d, "package-lock.json"), "w") as fh:
                fh.write("{}")
            with open(os.path.join(d, "yarn.lock"), "w") as fh:
                fh.write('left-pad@1.3.0:\n  version "1.3.0"\n  integrity sha512-x\n')
            index = {d: {"package.json", "package-lock.json", "yarn.lock"}}
            findings = PackageJsonChecker().check(index, d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        assert [(f.path, f.message) for f in findings] == [
            (
                "package-lock.json",
                "package-lock.json contains no packages (empty or unrecognised format)",
            )
        ]

    def test_stale_message_names_every_lock(self):
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, "package.json"), "w") as fh:
                json.dump({"dependencies": {"a": "1.0.0", "b": "1.0.0"}}, fh)
            with open(os.path.join(d, "package-lock.json"), "w") as fh:
                json.dump({"packages": {"node_modules/a": {}}}, fh)
            with open(os.path.join(d, "yarn.lock"), "w") as fh:
                fh.write("a@1.0.0:\n  version 1.0.0\n")
            index = {d: {"package.json", "package-lock.json", "yarn.lock"}}
            findings = PackageJsonChecker().check(index, d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        assert [f.message for f in findings] == [
            "package-lock.json, yarn.lock are stale: missing 1 dependency (b)"
        ]
