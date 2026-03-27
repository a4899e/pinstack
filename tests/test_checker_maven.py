"""Tests for the Maven pom.xml checker."""

import os

from pinstack.checkers.maven import MavenChecker

MAVEN_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "maven")


def _check(subpath):
    # type: (str) -> list
    dirpath = os.path.join(MAVEN_FIXTURES, subpath)
    files = set(os.listdir(dirpath))
    index = {dirpath: files}
    return MavenChecker().check(index, dirpath)


def _check_empty():
    # type: () -> list
    return MavenChecker().check({}, "/tmp")


class TestMavenGoodPom:
    def test_good_pom_no_findings(self):
        findings = _check("good_pom")
        assert findings == [], (
            "All explicit versions should produce 0 findings, got: {}".format(
                [f.message for f in findings]
            )
        )


class TestMavenMissingVersion:
    def setup_method(self):
        self.findings = _check("missing_version")

    def test_one_finding(self):
        assert len(self.findings) == 1, (
            "Missing <version> should produce 1 finding, got: {}".format(
                [f.message for f in self.findings]
            )
        )

    def test_message_mentions_version(self):
        assert "version" in self.findings[0].message.lower()

    def test_checker_name(self):
        assert self.findings[0].checker == "maven"

    def test_path_is_relative(self):
        assert not os.path.isabs(self.findings[0].path)


class TestMavenVersionRange:
    def setup_method(self):
        self.findings = _check("version_range")

    def test_one_finding(self):
        assert len(self.findings) == 1, (
            "Version range should produce 1 finding, got: {}".format(
                [f.message for f in self.findings]
            )
        )

    def test_message_mentions_range(self):
        assert (
            "range" in self.findings[0].message.lower()
            or "[" in self.findings[0].message
        )

    def test_checker_name(self):
        assert self.findings[0].checker == "maven"

    def test_has_line_number(self):
        assert self.findings[0].line > 0


class TestMavenLatestVersion:
    def setup_method(self):
        self.findings = _check("latest_version")

    def test_one_finding(self):
        assert len(self.findings) == 1, (
            "LATEST version should produce 1 finding, got: {}".format(
                [f.message for f in self.findings]
            )
        )

    def test_message_mentions_latest(self):
        assert "LATEST" in self.findings[0].message

    def test_checker_name(self):
        assert self.findings[0].checker == "maven"


class TestMavenReleaseVersion:
    def setup_method(self):
        self.findings = _check("release_version")

    def test_one_finding(self):
        assert len(self.findings) == 1, (
            "RELEASE version should produce 1 finding, got: {}".format(
                [f.message for f in self.findings]
            )
        )

    def test_message_mentions_release(self):
        assert "RELEASE" in self.findings[0].message

    def test_checker_name(self):
        assert self.findings[0].checker == "maven"


class TestMavenSnapshotVersion:
    def setup_method(self):
        self.findings = _check("snapshot_version")

    def test_one_finding(self):
        assert len(self.findings) == 1, (
            "SNAPSHOT version should produce 1 finding, got: {}".format(
                [f.message for f in self.findings]
            )
        )

    def test_message_mentions_snapshot(self):
        assert "SNAPSHOT" in self.findings[0].message

    def test_checker_name(self):
        assert self.findings[0].checker == "maven"

    def test_has_line_number(self):
        assert self.findings[0].line > 0


class TestMavenPropertyVersion:
    def test_property_version_flagged(self):
        findings = _check("property_version")
        assert len(findings) == 1, (
            "Property reference version should produce 1 finding, got: {!r}".format(
                [f.message for f in findings]
            )
        )
        assert "property reference" in findings[0].message, (
            "Finding message should mention 'property reference', got: {!r}".format(
                findings[0].message
            )
        )


class TestMavenMultipleBadDeps:
    def setup_method(self):
        self.findings = _check("multiple_bad")

    def test_correct_count(self):
        # 4 bad deps: LATEST, version range, missing version, SNAPSHOT
        assert len(self.findings) == 4, "Expected 4 findings, got {}: {}".format(
            len(self.findings), [f.message for f in self.findings]
        )

    def test_all_maven_checker(self):
        for f in self.findings:
            assert f.checker == "maven"

    def test_all_relative_paths(self):
        for f in self.findings:
            assert not os.path.isabs(f.path)


class TestMavenNoPom:
    def test_empty_dir_no_findings(self):
        assert _check_empty() == []

    def test_no_pom_in_index(self):
        # index has non-pom files — should still produce 0 findings
        findings = MavenChecker().check({"/tmp": {"build.gradle", "README.md"}}, "/tmp")
        assert findings == []


class TestMavenWithNamespace:
    def setup_method(self):
        self.findings = _check("with_namespace")

    def test_namespace_pom_finds_bad_dep(self):
        # with_namespace/pom.xml has 1 dep with LATEST
        assert len(self.findings) == 1, (
            "Namespace POM with 1 bad dep should produce 1 finding, got {}: {}".format(
                len(self.findings), [f.message for f in self.findings]
            )
        )

    def test_namespace_pom_correct_message(self):
        assert "LATEST" in self.findings[0].message

    def test_checker_name(self):
        assert self.findings[0].checker == "maven"
