"""Tests for the Maven pom.xml checker."""

import codecs
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


def _scan(subpath):
    # type: (str) -> list
    """Index a fixture tree the way the CLI does and run the checker over it."""
    from pinstack.core import build_index

    dirpath = os.path.join(MAVEN_FIXTURES, subpath)
    index = build_index(dirpath, set(MavenChecker.patterns))
    return [
        (f.path.replace(os.sep, "/"), f.line, f.message)
        for f in MavenChecker().check(index, dirpath)
    ]


def _check_text(text, fname="pom.xml"):
    # type: (str, str) -> list
    import shutil
    import tempfile

    d = tempfile.mkdtemp()
    try:
        with open(os.path.join(d, fname), "w") as fh:
            fh.write(text)
        return [(f.line, f.message) for f in MavenChecker().check({d: {fname}}, d)]
    finally:
        shutil.rmtree(d, ignore_errors=True)


class TestMavenModel410:
    """Review repro M6a/b: a 4.1.0-namespace POM yielded zero dependencies."""

    def test_namespace_and_plugin_without_version(self):
        assert _scan("model_410") == [
            ("pom.xml", 2, "'g:a' uses 'LATEST'; pin to an exact version"),
            (
                "pom.xml",
                3,
                "plugin 'g:p' has no <version>; specify an explicit version",
            ),
        ]

    def test_no_namespace_is_read_too(self):
        findings = _check_text(
            "<project><dependencies><dependency><groupId>g</groupId>"
            "<artifactId>a</artifactId><version>RELEASE</version>"
            "</dependency></dependencies></project>"
        )
        assert findings == [(1, "'g:a' uses 'RELEASE'; pin to an exact version")]


class TestMavenBuildElements:
    """Review repro M6b: plugins, extensions and parent resolve versions too."""

    def setup_method(self):
        self.findings = _scan("build_elements")
        self.messages = [m for _, _, m in self.findings]

    def test_every_unpinned_element_reported(self):
        assert [line for _, line, _ in self.findings] == [7, 25, 33, 41, 46, 57, 65]

    def test_parent_range(self):
        assert self.messages[0] == (
            "parent 'com.example:parent' uses a version range '[1.0,2.0)';"
            " pin to an exact version"
        )

    def test_partial_property_reference(self):
        assert "'1.${minor}' which cannot be verified" in self.messages[1]

    def test_build_extension_snapshot(self):
        assert self.messages[2].startswith("extension 'org.ext:wagon' uses SNAPSHOT")

    def test_plugin_management_property(self):
        assert self.messages[3].startswith(
            "plugin 'org.ok:managed' version uses property reference"
        )

    def test_plugin_default_group(self):
        """A plugin without groupId is org.apache.maven.plugins."""
        assert self.messages[4] == (
            "plugin 'org.apache.maven.plugins:maven-compiler-plugin' has no"
            " <version>; specify an explicit version"
        )

    def test_plugin_dependency(self):
        assert self.messages[5] == (
            "'org.tool:helper' uses 'RELEASE'; pin to an exact version"
        )

    def test_reporting_plugin(self):
        assert self.messages[6].startswith("plugin 'org.report:site-report' has no")

    def test_exclusions_are_not_coordinates(self):
        assert not any("org.unwanted" in m for m in self.messages)


class TestMavenParent:
    def test_model_410_parent_version_is_inferred(self):
        """Maven 4 takes a 4.1.0 parent's version from the local parent POM."""
        assert _scan("parent_410") == []

    def test_model_400_parent_needs_a_version(self):
        findings = _check_text(
            '<project xmlns="http://maven.apache.org/POM/4.0.0">'
            "<modelVersion>4.0.0</modelVersion><parent><groupId>g</groupId>"
            "<artifactId>p</artifactId></parent></project>"
        )
        assert findings == [
            (1, "parent 'g:p' has no <version>; specify an explicit version")
        ]


class TestMavenCoreExtensions:
    def test_mvn_extensions_xml_checked(self):
        assert _scan("core_extensions") == [
            (
                ".mvn/extensions.xml",
                11,
                "extension 'com.example:floating-extension' uses 'LATEST';"
                " pin to an exact version",
            )
        ]

    def test_extensions_xml_elsewhere_ignored(self):
        assert _check_text("<extensions/>", fname="extensions.xml") == []


class TestMavenDtdRefused:
    """Review M6c: the docstring claimed entity restrictions the code lacked."""

    def test_entity_declarations_are_refused_not_expanded(self):
        assert _scan("dtd") == [
            (
                "pom.xml",
                0,
                "pom.xml has a DOCTYPE, which pinstack does not parse; remove it",
            )
        ]

    def test_bare_doctype_refused(self):
        findings = _check_text(
            '<?xml version="1.0"?>\n<!DOCTYPE project SYSTEM "http://example.invalid/x.dtd">\n<project/>\n'
        )
        assert [m for _, m in findings] == [
            "pom.xml has a DOCTYPE, which pinstack does not parse; remove it"
        ]


def _check_bytes(data, fname="pom.xml"):
    # type: (bytes, str) -> list
    import shutil
    import tempfile

    d = tempfile.mkdtemp()
    try:
        with open(os.path.join(d, fname), "wb") as fh:
            fh.write(data)
        return [(f.line, f.message) for f in MavenChecker().check({d: {fname}}, d)]
    finally:
        shutil.rmtree(d, ignore_errors=True)


_LATEST_DEP = (
    "<dependencies><dependency><groupId>g</groupId><artifactId>a</artifactId>"
    "<version>LATEST</version></dependency></dependencies>"
)


class TestMavenReviewFollowUps:
    """Local review of the M6 rewrite."""

    def test_shift_jis_pom_is_checked(self):
        data = (
            '<?xml version="1.0" encoding="Shift_JIS"?>\n<project><name>'
            "テスト</name>" + _LATEST_DEP + "</project>"
        ).encode("shift_jis")
        assert _check_bytes(data) == [
            (2, "'g:a' uses 'LATEST'; pin to an exact version")
        ]

    def test_unknown_encoding_falls_back_to_utf8(self):
        data = (
            '<?xml version="1.0" encoding="x-made-up"?>\n<project>'
            + _LATEST_DEP
            + "</project>"
        ).encode()
        assert [m for _, m in _check_bytes(data)] == [
            "'g:a' uses 'LATEST'; pin to an exact version"
        ]

    def test_undeclared_latin1_bytes_do_not_stop_the_check(self):
        data = b"<project><name>Jos\xe9</name>" + _LATEST_DEP.encode() + b"</project>"
        assert [m for _, m in _check_bytes(data)] == [
            "'g:a' uses 'LATEST'; pin to an exact version"
        ]

    def test_deep_nesting_does_not_crash(self):
        depth = 3000
        text = "<project>" + "<a>" * depth + "</a>" * depth + _LATEST_DEP + "</project>"
        assert [m for _, m in _check_text(text)] == [
            "'g:a' uses 'LATEST'; pin to an exact version"
        ]

    def test_large_text_parses_in_linear_time(self):
        import time

        def run(n):
            body = "x &amp; y\n" * n
            text = (
                "<project><build><plugins><plugin><artifactId>p</artifactId>"
                "<version>1</version><configuration><script>"
                + body
                + "</script></configuration></plugin></plugins></build></project>"
            )
            start = time.perf_counter()
            _check_text(text)
            return time.perf_counter() - start

        small, big = run(20000), run(80000)
        assert big < small * 12, (small, big)

    def test_plugin_managed_in_same_pom(self):
        """build/pluginManagement in the same file is checked, so it excuses the build entry."""
        text = (
            "<project><build><pluginManagement><plugins><plugin>"
            "<artifactId>maven-surefire-plugin</artifactId><version>3.2.5</version>"
            "</plugin></plugins></pluginManagement><plugins><plugin>"
            "<artifactId>maven-surefire-plugin</artifactId></plugin></plugins>"
            "</build></project>"
        )
        assert _check_text(text) == []

    def test_plugin_managed_by_parent_is_reported(self):
        """A parent's management cannot be verified from the child, so both are reported."""
        assert [(p, m) for p, _, m in _scan("parent_chain")] == [
            (
                "child/pom.xml",
                "plugin 'org.apache.maven.plugins:maven-surefire-plugin' has no"
                " <version>; specify an explicit version",
            ),
            (
                "child/pom.xml",
                "plugin 'org.apache.maven.plugins:maven-jar-plugin' has no"
                " <version>; specify an explicit version",
            ),
        ]

    def test_profile_management_does_not_excuse(self):
        """A profile's pluginManagement applies only when the profile is active."""
        text = (
            "<project><build><plugins><plugin><artifactId>maven-jar-plugin</artifactId>"
            "</plugin></plugins></build><profiles><profile><id>p</id><build>"
            "<pluginManagement><plugins><plugin><artifactId>maven-jar-plugin</artifactId>"
            "<version>3.3.0</version></plugin></plugins></pluginManagement></build>"
            "</profile></profiles></project>"
        )
        assert [m for _, m in _check_text(text)] == [
            "plugin 'org.apache.maven.plugins:maven-jar-plugin' has no <version>;"
            " specify an explicit version"
        ]

    def test_dependency_managed_in_same_pom(self):
        text = (
            "<project><dependencyManagement><dependencies><dependency>"
            "<groupId>g</groupId><artifactId>a</artifactId><version>1.0</version>"
            "</dependency></dependencies></dependencyManagement><dependencies>"
            "<dependency><groupId>g</groupId><artifactId>a</artifactId></dependency>"
            "<dependency><groupId>g</groupId><artifactId>b</artifactId></dependency>"
            "</dependencies></project>"
        )
        assert [m for _, m in _check_text(text)] == [
            "'g:b' has no <version>; specify an explicit version"
        ]

    def test_non_text_codec_declaration(self):
        data = (
            '<?xml version="1.0" encoding="base64"?>\n<project>'
            + _LATEST_DEP
            + "</project>"
        ).encode()
        assert [m for _, m in _check_bytes(data)] == [
            "'g:a' uses 'LATEST'; pin to an exact version"
        ]

    def test_utf32_and_bomless_utf16(self):
        text = '<?xml version="1.0"?>\n<project>' + _LATEST_DEP + "</project>"
        for data in (
            codecs.BOM_UTF32_LE + text.encode("utf-32-le"),
            text.encode("utf-16-le"),
            text.encode("utf-16-be"),
        ):
            assert [m for _, m in _check_bytes(data)] == [
                "'g:a' uses 'LATEST'; pin to an exact version"
            ], data[:8]

    def test_configuration_contents_are_not_coordinates(self):
        text = (
            "<project><build><plugins><plugin><groupId>g</groupId>"
            "<artifactId>p</artifactId><version>1.0</version><configuration>"
            "<plugins><plugin><name>internal</name></plugin></plugins>"
            "<dependencies><dependency><artifactId>x</artifactId></dependency>"
            "</dependencies></configuration></plugin></plugins></build></project>"
        )
        assert _check_text(text) == []

    def test_configuration_artifacts_are_checked(self):
        text = (
            "<project><build><plugins><plugin><artifactId>maven-compiler-plugin</artifactId>"
            "<version>3.13.0</version><configuration><annotationProcessorPaths><path>"
            "<groupId>org.projectlombok</groupId><artifactId>lombok</artifactId>"
            "<version>LATEST</version></path></annotationProcessorPaths>"
            "</configuration></plugin><plugin><artifactId>maven-site-plugin</artifactId>"
            "<version>3.12.1</version><configuration><reportPlugins><plugin>"
            "<artifactId>maven-pmd-plugin</artifactId></plugin></reportPlugins>"
            "</configuration></plugin></plugins></build></project>"
        )
        assert [m for _, m in _check_text(text)] == [
            "plugin configuration artifact 'org.projectlombok:lombok' uses"
            " 'LATEST'; pin to an exact version",
            "plugin 'org.apache.maven.plugins:maven-pmd-plugin' has no <version>;"
            " specify an explicit version",
        ]

    def test_unexpected_root_is_reported(self):
        text = "<projekt>" + _LATEST_DEP + "</projekt>"
        assert _check_text(text) == [
            (
                1,
                "pom.xml has root element <projekt>, not <project>, so it was not checked",
            )
        ]

    def test_model_410_reactor_dependency_without_group(self):
        """Maven 4 infers the version of a sibling module written without groupId."""
        text = (
            '<project xmlns="http://maven.apache.org/POM/4.1.0"><dependencies>'
            "<dependency><artifactId>sibling</artifactId></dependency>"
            "<dependency><groupId>org.ext</groupId><artifactId>lib</artifactId>"
            "</dependency></dependencies></project>"
        )
        assert [m for _, m in _check_text(text)] == [
            "'org.ext:lib' has no <version>; specify an explicit version"
        ]
