"""Tests for the Gradle build.gradle / build.gradle.kts checker."""

import os

from pinstack.checkers.gradle import GradleChecker

GRADLE_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "gradle")


def _check(subpath):
    # type: (str) -> list
    dirpath = os.path.join(GRADLE_FIXTURES, subpath)
    files = set(os.listdir(dirpath))
    index = {dirpath: files}
    return GradleChecker().check(index, dirpath)


def _check_empty():
    # type: () -> list
    return GradleChecker().check({}, "/tmp")


class TestGradleGood:
    def test_good_build_gradle_no_findings(self):
        # good/ has both build.gradle and gradle.lockfile with pinned versions
        findings = _check("good")
        assert findings == [], "Pinned deps with lockfile should produce 0 findings, got: {}".format(
            [f.message for f in findings]
        )


class TestGradleDynamicVersionPlus:
    def setup_method(self):
        self.findings = _check("dynamic_plus")

    def test_one_version_finding(self):
        # dynamic_plus has gradle.lockfile so no lock finding; 1 dynamic version finding
        version_findings = [f for f in self.findings if "+" in f.message or "dynamic" in f.message.lower()]
        assert len(version_findings) == 1, (
            "31.+ dynamic version should produce 1 finding, got: {}".format(
                [f.message for f in self.findings]
            )
        )

    def test_message_mentions_plus(self):
        msgs = [f.message for f in self.findings]
        assert any("+" in m for m in msgs)

    def test_checker_name(self):
        for f in self.findings:
            assert f.checker == "gradle"

    def test_has_line_number(self):
        for f in self.findings:
            assert f.line > 0


class TestGradleLatestRelease:
    def setup_method(self):
        self.findings = _check("latest_release")

    def test_one_finding(self):
        assert len(self.findings) == 1, (
            "latest.release should produce 1 finding, got: {}".format(
                [f.message for f in self.findings]
            )
        )

    def test_message_mentions_latest(self):
        assert "latest" in self.findings[0].message.lower()

    def test_checker_name(self):
        assert self.findings[0].checker == "gradle"


class TestGradleVersionRange:
    def setup_method(self):
        self.findings = _check("version_range")

    def test_one_finding(self):
        assert len(self.findings) == 1, (
            "Version range should produce 1 finding, got: {}".format(
                [f.message for f in self.findings]
            )
        )

    def test_message_mentions_range(self):
        assert "range" in self.findings[0].message.lower() or "[" in self.findings[0].message

    def test_checker_name(self):
        assert self.findings[0].checker == "gradle"


class TestGradleMissingVersion:
    def setup_method(self):
        self.findings = _check("missing_version")

    def test_one_finding(self):
        assert len(self.findings) == 1, (
            "Missing version should produce 1 finding, got: {}".format(
                [f.message for f in self.findings]
            )
        )

    def test_message_mentions_version(self):
        assert "version" in self.findings[0].message.lower()

    def test_checker_name(self):
        assert self.findings[0].checker == "gradle"


class TestGradleKotlinDsl:
    def setup_method(self):
        self.findings = _check("kotlin_dsl")

    def test_one_finding(self):
        # kotlin_dsl has build.gradle.kts with version+; gradle.lockfile present
        assert len(self.findings) == 1, (
            "Kotlin DSL with version+ should produce 1 finding, got: {}".format(
                [f.message for f in self.findings]
            )
        )

    def test_message_mentions_plus(self):
        assert "+" in self.findings[0].message

    def test_checker_name(self):
        assert self.findings[0].checker == "gradle"

    def test_path_ends_with_kts(self):
        assert self.findings[0].path.endswith("build.gradle.kts")


class TestGradleNoLockfile:
    def setup_method(self):
        self.findings = _check("no_lockfile")

    def test_one_finding_for_missing_lock(self):
        assert len(self.findings) == 1, (
            "build.gradle without gradle.lockfile should produce 1 finding, got: {}".format(
                [f.message for f in self.findings]
            )
        )

    def test_message_mentions_lockfile(self):
        assert "gradle.lockfile" in self.findings[0].message

    def test_checker_name(self):
        assert self.findings[0].checker == "gradle"

    def test_path_is_relative(self):
        assert not os.path.isabs(self.findings[0].path)


class TestGradleWithLockfile:
    def test_build_gradle_with_lockfile_no_lock_finding(self):
        findings = _check("with_lockfile")
        lock_findings = [f for f in findings if "gradle.lockfile" in f.message]
        assert lock_findings == [], (
            "build.gradle + gradle.lockfile should produce no lock-related findings, got: {}".format(
                [f.message for f in lock_findings]
            )
        )

    def test_total_findings_zero(self):
        # with_lockfile has pinned versions AND lockfile
        findings = _check("with_lockfile")
        assert findings == [], "Pinned deps + lockfile = 0 findings, got: {}".format(
            [f.message for f in findings]
        )


class TestGradleNoGradle:
    def test_empty_index_no_findings(self):
        assert _check_empty() == []

    def test_no_gradle_files_no_findings(self):
        findings = GradleChecker().check({"/tmp": {"pom.xml", "README.md"}}, "/tmp")
        assert findings == []


class TestGradleMultipleConfigurations:
    def setup_method(self):
        self.findings = _check("multi_config")

    def test_all_configurations_checked(self):
        # multi_config has 4 bad deps: implementation(5.+), api(latest.release),
        # testImplementation(latest.integration), runtimeOnly(3.+)
        assert len(self.findings) == 4, (
            "Expected 4 findings across multiple configurations, got {}: {}".format(
                len(self.findings), [f.message for f in self.findings]
            )
        )

    def test_all_gradle_checker(self):
        for f in self.findings:
            assert f.checker == "gradle"

    def test_all_relative_paths(self):
        for f in self.findings:
            assert not os.path.isabs(f.path)

    def test_all_have_line_numbers(self):
        for f in self.findings:
            assert f.line > 0
