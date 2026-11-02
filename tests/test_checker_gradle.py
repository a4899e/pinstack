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
        assert findings == [], (
            "Pinned deps with lockfile should produce 0 findings, got: {}".format(
                [f.message for f in findings]
            )
        )


class TestGradleDynamicVersionPlus:
    def setup_method(self):
        self.findings = _check("dynamic_plus")

    def test_one_version_finding(self):
        # dynamic_plus has gradle.lockfile so no lock finding; 1 dynamic version finding
        version_findings = [
            f
            for f in self.findings
            if "+" in f.message or "dynamic" in f.message.lower()
        ]
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
        assert (
            "range" in self.findings[0].message.lower()
            or "[" in self.findings[0].message
        )

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


class TestGradleCrossRef:
    """Cross-reference: build.gradle pinned deps must all appear in gradle.lockfile."""

    def _make_index(
        self, build_gradle_name, build_gradle_content, lockfile_content=None
    ):
        # type: (str, str, str) -> list
        import tempfile

        d = tempfile.mkdtemp()
        build_file = os.path.join(d, build_gradle_name)
        with open(build_file, "w") as fh:
            fh.write(build_gradle_content)
        files = {build_gradle_name}
        if lockfile_content is not None:
            lockfile = os.path.join(d, "gradle.lockfile")
            with open(lockfile, "w") as fh:
                fh.write(lockfile_content)
            files.add("gradle.lockfile")
        index = {d: files}
        return GradleChecker().check(index, d)

    def test_dep_in_build_gradle_missing_from_lockfile(self):
        build_gradle = (
            "dependencies {\n    implementation 'com.google.guava:guava:31.1-jre'\n}\n"
        )
        lockfile = (
            "# Gradle lock file\n"
            "org.slf4j:slf4j-api:2.0.9=compileClasspath,runtimeClasspath\n"
            "empty=\n"
        )
        findings = self._make_index("build.gradle", build_gradle, lockfile)
        cross_ref = [f for f in findings if "stale" in f.message]
        assert len(cross_ref) == 1, "Expected 1 cross-ref finding, got: {}".format(
            [f.message for f in findings]
        )
        assert "com.google.guava:guava" in cross_ref[0].message
        assert cross_ref[0].checker == "gradle"

    def test_dep_in_build_gradle_present_in_lockfile(self):
        build_gradle = (
            "dependencies {\n    implementation 'com.google.guava:guava:31.1-jre'\n}\n"
        )
        lockfile = (
            "# Gradle lock file\n"
            "com.google.guava:guava:31.1-jre=compileClasspath,runtimeClasspath\n"
            "empty=\n"
        )
        findings = self._make_index("build.gradle", build_gradle, lockfile)
        cross_ref = [f for f in findings if "stale" in f.message]
        assert cross_ref == [], (
            "Dep present in lockfile — expected no cross-ref findings, got: {}".format(
                [f.message for f in findings]
            )
        )

    def test_kotlin_dsl_cross_ref(self):
        build_gradle_kts = (
            'dependencies {\n    implementation("com.google.guava:guava:31.1-jre")\n}\n'
        )
        lockfile = (
            "# Gradle lock file\n"
            "org.slf4j:slf4j-api:2.0.9=compileClasspath,runtimeClasspath\n"
            "empty=\n"
        )
        findings = self._make_index("build.gradle.kts", build_gradle_kts, lockfile)
        cross_ref = [f for f in findings if "stale" in f.message]
        assert len(cross_ref) == 1, (
            "Expected 1 cross-ref finding for Kotlin DSL, got: {}".format(
                [f.message for f in findings]
            )
        )
        assert "com.google.guava:guava" in cross_ref[0].message

    def test_multiple_deps_some_missing(self):
        build_gradle = (
            "dependencies {\n"
            "    implementation 'com.google.guava:guava:31.1-jre'\n"
            "    implementation 'org.slf4j:slf4j-api:2.0.9'\n"
            "    implementation 'org.apache.commons:commons-lang3:3.12.0'\n"
            "}\n"
        )
        lockfile = (
            "# Gradle lock file\n"
            "com.google.guava:guava:31.1-jre=compileClasspath,runtimeClasspath\n"
            "empty=\n"
        )
        findings = self._make_index("build.gradle", build_gradle, lockfile)
        cross_ref = [f for f in findings if "stale" in f.message]
        assert len(cross_ref) == 1, (
            "Expected 1 summary cross-ref finding, got: {}".format(
                [f.message for f in findings]
            )
        )
        assert "org.slf4j:slf4j-api" in cross_ref[0].message
        assert "org.apache.commons:commons-lang3" in cross_ref[0].message


class TestGradleMapNotationPinned:
    def test_map_notation_pinned_no_version_findings(self):
        """Map-notation dep with a pinned version and a lockfile should produce 0 findings."""
        findings = _check("map_notation_pinned")
        version_findings = [
            f
            for f in findings
            if "version" in f.message.lower() or "dynamic" in f.message.lower()
        ]
        assert version_findings == [], (
            "Pinned map-notation dep should produce 0 version findings, got: {}".format(
                [f.message for f in version_findings]
            )
        )


class TestGradleMapNotationDynamic:
    def setup_method(self):
        self.findings = _check("map_notation_dynamic")

    def test_map_notation_dynamic_flagged(self):
        """Map-notation dep with a dynamic version should produce 1 finding."""
        assert len(self.findings) == 1, (
            "Dynamic map-notation dep should produce 1 finding, got {}: {}".format(
                len(self.findings), [f.message for f in self.findings]
            )
        )

    def test_message_mentions_plus(self):
        assert "+" in self.findings[0].message

    def test_checker_name(self):
        assert self.findings[0].checker == "gradle"

    def test_has_line_number(self):
        assert self.findings[0].line > 0


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


def _scan(subpath):
    # type: (str) -> list
    """Index a fixture tree the way the CLI does and run the checker over it."""
    from pinstack.core import build_index

    dirpath = os.path.join(GRADLE_FIXTURES, subpath)
    index = build_index(dirpath, set(GradleChecker.patterns))
    return [
        (f.path.replace(os.sep, "/"), f.line, f.message)
        for f in GradleChecker().check(index, dirpath)
    ]


def _decls(text):
    # type: (str) -> list
    from pinstack.checkers.gradle import _declarations

    return [d[2:] for d in _declarations(text.splitlines(True))[0]]


class TestGradleModernBuild:
    """Review repro M5: most of a modern build was invisible."""

    def setup_method(self):
        self.findings = _scan("modern_build")
        self.by_line = {(p, n): m for p, n, m in self.findings}

    def test_five_findings(self):
        assert sorted(self.by_line) == [
            ("build.gradle", 3),
            ("build.gradle", 4),
            ("build.gradle", 5),
            ("build.gradle", 6),
            ("deps.gradle", 1),
        ], self.findings

    def test_variable_reference(self):
        assert "variable reference '$v'" in self.by_line[("build.gradle", 3)]

    def test_snapshot(self):
        assert "SNAPSHOT version '1.0-SNAPSHOT'" in self.by_line[("build.gradle", 4)]

    def test_platform_wrapper(self):
        assert (
            "'org.foo:bom' uses dynamic version '1.+'"
            in (self.by_line[("build.gradle", 5)])
        )

    def test_kotlin_named_arguments(self):
        assert "'org.foo:lib3'" in self.by_line[("build.gradle", 6)]

    def test_applied_script_checked(self):
        """`apply from: "deps.gradle"` pulls in a script that is now indexed."""
        assert "'org.foo:lib4'" in self.by_line[("deps.gradle", 1)]

    def test_applied_script_needs_no_lockfile(self):
        """The lockfile requirement is keyed on build.gradle(.kts) alone."""
        assert not any("no gradle.lockfile" in m for _, _, m in self.findings)


class TestGradleDeclarationForms:
    def test_enforced_platform_and_parenthesised_wrapper(self):
        assert _decls(
            'implementation(enforcedPlatform("g:a:1.0"))\n'
            'api platform("g:b:2.0")\n'
            'testImplementation(testFixtures("g:c:3.0"))\n'
        ) == [("g", "a", "1.0"), ("g", "b", "2.0"), ("g", "c", "3.0")]

    def test_kotlin_template_not_split(self):
        assert _decls('implementation("g:a:${v}")\n') == [("g", "a", "${v}")]

    def test_several_declarations_on_one_line(self):
        assert _decls('dependencies { implementation "g:a:1"; api "g:b:2" }\n') == [
            ("g", "a", "1"),
            ("g", "b", "2"),
        ]

    def test_commented_out_declaration_ignored(self):
        assert (
            _decls('// implementation "g:a:+"\ndependencies { // api "g:b:+"\n') == []
        )

    def test_map_notation_any_order_and_missing_version(self):
        assert _decls(
            "implementation version: '1', name: 'a', group: 'g'\n"
            'compileOnly(name = "b", group = "g")\n'
        ) == [("g", "a", "1"), ("g", "b", None)]

    def test_classpath_configuration(self):
        assert _decls('classpath "com.android.tools.build:gradle:8.+"\n') == [
            ("com.android.tools.build", "gradle", "8.+")
        ]

    def test_bad_versions(self):
        from pinstack.checkers.gradle import _is_bad_version

        assert "variable" in _is_bad_version("${v}")
        assert "SNAPSHOT" in _is_bad_version("1.0-snapshot")
        assert _is_bad_version("1.0.0") == ""


class TestGradleMapNotationLinearTime:
    """Review L4: the map-notation regex was quadratic in line length."""

    def test_long_line_parses_in_linear_time(self):
        """Quadrupling the line should take ~4x as long, not ~16x."""
        import time

        def timed(n):
            line = "implementation name: 'y' " + "group: 'g', " * n
            start = time.perf_counter()
            assert _decls(line) == [("g", "y", None)]
            return time.perf_counter() - start

        small, large = timed(20000), timed(80000)  # ~235 KB, ~940 KB
        assert large < 8 * small + 0.05, (small, large)


class TestGradleVersionCatalog:
    """Review M5f: gradle/libs.versions.toml was not read."""

    def setup_method(self):
        self.findings = _scan("catalog")

    def test_findings(self):
        assert [(n, m.split("'")[1]) for _, n, m in self.findings] == [
            (3, "junit"),
            (4, "dyn"),
            (8, "lib5"),
            (10, "lib7"),
            (13, "p"),
        ]
        assert all(p == "gradle/libs.versions.toml" for p, _, _ in self.findings)

    def test_messages(self):
        by_line = {n: m for _, n, m in self.findings}
        assert by_line[3] == (
            "'junit' in [versions] uses version range '[4.0,5.0)'; pin to an exact version"
        )
        assert by_line[10] == (
            "'lib7' in [libraries] has no version specified; pin to an exact version"
        )

    def test_version_ref_and_inline_pins_accepted(self):
        """guava uses version.ref (checked in [versions]); lib6 is pinned inline."""
        names = [m.split("'")[1] for _, _, m in self.findings]
        assert "guava" not in names
        assert "lib6" not in names


class TestGradleReviewFollowUps:
    def test_commented_out_after_brace_or_semicolon(self):
        assert _decls(
            '// dependencies { implementation "g:x:+" }\n'
            '// TODO; implementation "g:y:+"\n'
            '/* implementation "g:z:+"\n   api "g:w:+" */\n'
            'implementation "g:a:1" // trailing { api "g:b:+" }\n'
        ) == [("g", "a", "1")]

    def test_comment_markers_inside_strings_kept(self):
        assert _decls("implementation \"g:a:1\" // x\nimplementation 'g:b:2'\n") == [
            ("g", "a", "1"),
            ("g", "b", "2"),
        ]

    def test_groovy_list_of_coordinates(self):
        assert _decls("implementation 'g:m1:1.0', 'g:m2:+'\n") == [
            ("g", "m1", "1.0"),
            ("g", "m2", "+"),
        ]

    def test_unquoted_map_version_is_a_variable(self):
        assert _decls(
            "implementation group: 'g', name: 'a', version: guavaVersion\n"
            'implementation(group = "g", name = "b", version = libs.versions.b.get())\n'
        ) == [("g", "a", "$guavaVersion"), ("g", "b", "$libs.versions.b.get()")]

    def test_classpath_checked_against_buildscript_lockfile(self):
        """Gradle locks the buildscript classpath in buildscript-gradle.lockfile."""
        import shutil
        import tempfile

        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, "build.gradle"), "w") as fh:
                fh.write(
                    'buildscript { dependencies { classpath "com.android.tools.build:gradle:8.1.0" } }\n'
                    'dependencies { implementation "g:a:1.0" }\n'
                )
            with open(os.path.join(d, "gradle.lockfile"), "w") as fh:
                fh.write("g:a:1.0=compileClasspath\n")
            index = {d: {"build.gradle", "gradle.lockfile"}}
            (missing,) = GradleChecker().check(index, d)
            assert missing.path == "build.gradle"
            assert "no buildscript-gradle.lockfile" in missing.message

            with open(os.path.join(d, "buildscript-gradle.lockfile"), "w") as fh:
                fh.write("other:x:1.0=classpath\n")
            index[d].add("buildscript-gradle.lockfile")
            (finding,) = GradleChecker().check(index, d)
            assert finding.path == "buildscript-gradle.lockfile"
            assert "com.android.tools.build:gradle" in finding.message
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestGradleCatalogForms:
    def _catalog(self, text):
        # type: (str) -> list
        import shutil
        import tempfile

        from pinstack.checkers.gradle import _catalog_findings

        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "libs.versions.toml")
            with open(path, "w") as fh:
                fh.write(text)
            return [
                (f.line, f.message.split("'")[1])
                for f in _catalog_findings(path, "c", "gradle")
            ]
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_inline_table_version_ref(self):
        assert (
            self._catalog(
                '[libraries]\nr = { module = "g:r", version = { ref = "a" } }\n'
            )
            == []
        )

    def test_sub_table_entries(self):
        assert self._catalog(
            "[libraries.tbl]\n"
            'module = "g:tbl"\n'
            'version = "1.+"\n'
            "[libraries.ok]\n"
            'module = "g:ok"\n'
            'version.ref = "x"\n'
            "[versions.rich]\n"
            'strictly = "[1,2)"\n'
            "[plugins.p]\n"
            'id = "x"\n'
        ) == [(1, "tbl"), (7, "rich"), (9, "p")]


class TestGradleCodexFollowUps:
    """Codex review of #15."""

    def _catalog(self, text):
        # type: (str) -> list
        return TestGradleCatalogForms()._catalog(text)

    def test_catalog_comment_cannot_suppress_a_version(self):
        assert self._catalog(
            '[libraries]\nfoo = { module = "g:a", version = "+" } # version.ref = "unused"\n'
        ) == [(2, "foo")]

    def test_catalog_subtable_comment_cannot_suppress_a_version(self):
        assert self._catalog(
            '[libraries.foo]\nmodule = "g:a"\nversion = "+"  # version.ref = "x"\n'
        ) == [(1, "foo")]

    def test_nested_rich_version_table(self):
        assert (
            self._catalog(
                '[libraries.foo]\nmodule = "g:a"\n[libraries.foo.version]\nstrictly = "1.0"\n'
            )
            == []
        )
        assert self._catalog(
            '[libraries.foo]\nmodule = "g:a"\n[libraries.foo.version]\nstrictly = "[1,2)"\n'
        ) == [(1, "foo")]
        assert (
            self._catalog(
                '[libraries.foo]\nmodule = "g:a"\n[libraries.foo.version]\nref = "x"\n'
            )
            == []
        )

    def test_multiline_kotlin_named_arguments(self):
        assert _decls(
            "dependencies {\n"
            "    implementation(\n"
            '        group = "g",\n'
            '        name = "a",\n'
            '        version = "1.+"\n'
            "    )\n"
            '    api("g:b:1.0")\n'
            "}\n"
        ) == [("g", "a", "1.+"), ("g", "b", "1.0")]

    def test_multiline_call_keeps_first_line_number(self):
        from pinstack.checkers.gradle import _declarations

        text = 'implementation(\n  "g:a:+"\n)\n'
        assert [d[0] for d in _declarations(text.splitlines(True))[0]] == [1]

    def test_quoted_alias_table_with_dots(self):
        """Codex #15 follow-up: `"foo.bar"` is one alias, not three key parts."""
        assert self._catalog(
            '[libraries."foo.bar"]\nmodule = "g:a"\nversion = "+"\n'
        ) == [(1, "foo.bar")]

    def test_quoted_inline_alias_and_dotted_fields(self):
        assert self._catalog(
            "[libraries]\n"
            '"foo.bar" = { module = "g:a", version = "+" }\n'
            'baz.module = "g:b"\n'
            'baz.version = "1.+"\n'
            'ok.module = "g:c"\n'
            'ok.version.ref = "x"\n'
        ) == [(2, "foo.bar"), (3, "baz")]

    def test_escaped_quote_does_not_open_a_call(self):
        """Codex #15 follow-up: `\\"(` inside a string is not a parenthesis."""
        assert _decls(
            'dependencies {\n  println("quote: \\"(")\n  implementation("g:a:+")\n}\n'
        ) == [("g", "a", "+")]

    def test_unrelated_open_call_does_not_hide_declarations(self):
        assert _decls('dependencies {\n  println(\n  implementation("g:a:+")\n}\n') == [
            ("g", "a", "+")
        ]

    def test_long_multiline_call_is_read_whole(self):
        """Codex #15 follow-up: blank lines inside a call must not truncate it."""
        text = (
            "dependencies {\n  implementation(\n"
            + "\n" * 60
            + '    group = "g", name = "a", version = "1.+"\n  )\n}\n'
        )
        assert _decls(text) == [("g", "a", "1.+")]

    def test_unclosed_dependency_call_reported(self):
        import shutil
        import tempfile

        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, "build.gradle.kts"), "w") as fh:
                fh.write('dependencies {\n  implementation(\n    "g:a:1.0"\n}\n')
            with open(os.path.join(d, "gradle.lockfile"), "w") as fh:
                fh.write("g:a:1.0=runtimeClasspath\n")
            index = {d: {"build.gradle.kts", "gradle.lockfile"}}
            findings = GradleChecker().check(index, d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        assert [(f.line, f.message) for f in findings] == [
            (
                2,
                "dependency declaration is never closed; it and the lines after"
                " it could not be checked",
            )
        ]

    def test_kotlin_raw_string_ending_in_backslash(self):
        """Codex #15 follow-up: Kotlin `\"\"\"\\\"\"\"` is raw; the backslash escapes nothing."""
        import shutil
        import tempfile

        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, "build.gradle.kts"), "w") as fh:
                fh.write(
                    "plugins { java }\n"
                    'dependencies { val marker = """\\"""; implementation("g:a:+") }\n'
                )
            with open(os.path.join(d, "gradle.lockfile"), "w") as fh:
                fh.write("g:a:1.0=compileClasspath\n")
            index = {d: {"build.gradle.kts", "gradle.lockfile"}}
            findings = GradleChecker().check(index, d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        assert [(f.line, f.message) for f in findings] == [
            (2, "'g:a' uses dynamic version '+' (+ wildcard); pin to an exact version")
        ]

    def test_groovy_triple_quoted_string_keeps_escapes(self):
        """Groovy triple-quoted strings do have escapes: `\\"` is content."""
        assert _decls(
            'dependencies {\n  def s = """a\\""""; implementation \'g:a:+\'\n}\n'
        ) == [("g", "a", "+")]

    def test_extra_quotes_before_raw_string_close(self):
        from pinstack.checkers.gradle import _declarations

        text = 'dependencies { val q = """a""""; implementation("g:a:+") }\n'
        decls, _ = _declarations(text.splitlines(True), raw_triple=True)
        assert [d[2:] for d in decls] == [("g", "a", "+")]
