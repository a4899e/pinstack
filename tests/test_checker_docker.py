"""Tests for dockerfile and compose checkers."""

from __future__ import annotations

import os
import shutil
import tempfile

from pinstack.checkers.dockerfile import DockerfileChecker
from pinstack.checkers.compose import ComposeChecker

DOCKER_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "docker")


def _check_dockerfile(subpath):
    # type: (str) -> list
    dirpath = os.path.join(DOCKER_FIXTURES, subpath)
    files = set(os.listdir(dirpath))
    index = {dirpath: files}
    return DockerfileChecker().check(index, dirpath)


def _check_compose(subpath, fname="docker-compose.yml"):
    # type: (str, str) -> list
    dirpath = os.path.join(DOCKER_FIXTURES, subpath)
    index = {dirpath: {fname}}
    return ComposeChecker().check(index, dirpath)


def _check_empty(checker):
    # type: (object,) -> list
    return checker.check({}, "/tmp")


# ---------------------------------------------------------------------------
# Dockerfile
# ---------------------------------------------------------------------------


class TestDockerfileGood:
    def test_sha256_pinned_no_findings(self):
        findings = _check_dockerfile("good")
        assert findings == [], "Digest-pinned FROM should produce 0 findings"

    def test_empty_index_no_findings(self):
        assert _check_empty(DockerfileChecker()) == []


class TestDockerfileBad:
    def setup_method(self):
        self.findings = _check_dockerfile("bad")

    def test_two_findings(self):
        assert len(self.findings) == 2, (
            "Expected 2 findings (python:3.11 and ubuntu:22.04), got {}: {}".format(
                len(self.findings), [f.message for f in self.findings]
            )
        )

    def test_has_two_findings(self):
        assert len(self.findings) == 2

    def test_python_flagged(self):
        msgs = [f.message for f in self.findings]
        assert any("python:3.11" in m for m in msgs)

    def test_ubuntu_flagged(self):
        msgs = [f.message for f in self.findings]
        assert any("ubuntu:22.04" in m for m in msgs)

    def test_checker_name(self):
        for f in self.findings:
            assert f.checker == "dockerfile"

    def test_paths_are_relative(self):
        for f in self.findings:
            assert not os.path.isabs(f.path)

    def test_line_numbers_positive(self):
        for f in self.findings:
            assert f.line > 0


class TestDockerfileScratch:
    def test_from_scratch_no_findings(self):
        findings = _check_dockerfile("scratch")
        assert findings == [], "FROM scratch should not produce findings"


class TestDockerfileNamed:
    def test_dockerfile_prod_checked(self):
        """Dockerfile.prod matches Dockerfile* pattern and should be checked."""
        dirpath = os.path.join(DOCKER_FIXTURES, "named")
        index = {dirpath: {"Dockerfile.prod"}}
        findings = DockerfileChecker().check(index, dirpath)
        assert findings == [], "Digest-pinned Dockerfile.prod should produce 0 findings"


class TestDockerfileMultistage:
    def test_declared_stage_references_skipped(self):
        """Declared stage names and indexes in FROM, COPY --from and RUN --mount are not images."""
        findings = _check_dockerfile("multistage")
        assert findings == [], (
            "Declared build stages should be skipped, got: {}".format(
                [f.message for f in findings]
            )
        )


def _run_dockerfile(content, fname="Dockerfile"):
    # type: (str, str) -> list
    d = tempfile.mkdtemp()
    try:
        with open(os.path.join(d, fname), "w") as fh:
            fh.write(content)
        return DockerfileChecker().check({d: {fname}}, d)
    finally:
        shutil.rmtree(d, ignore_errors=True)


_DIGEST = "@sha256:" + "a1b2c3d4" * 8


class TestDockerfileUnpinnedForms:
    """Image references that are pulled unpinned even though they look harmless."""

    def setup_method(self):
        self.findings = _check_dockerfile("unpinned_forms")
        self.by_line = {f.line: f.message for f in self.findings}

    def test_six_findings(self):
        """Bare image, $VAR, ${VAR}, continued FROM, a malformed digest, and COPY --from=<image>."""
        assert sorted(self.by_line) == [2, 3, 4, 5, 7, 8], self.by_line

    def test_short_digest_is_malformed(self):
        """Review L3: `@sha256:aaaa` is not a digest; it names no single image."""
        assert self.by_line[7] == (
            "FROM 'golang@sha256:aaaa' has a malformed @sha256: digest; expected"
            " 64 lowercase hex characters at the end of the reference"
        )

    def test_bare_image_is_not_a_stage(self):
        """`FROM alpine` names no declared stage, so it pulls alpine:latest."""
        assert "'alpine' is not pinned" in self.by_line[2]

    def test_variable_references_unverifiable(self):
        """ARG-interpolated bases can be overridden with --build-arg."""
        assert "'$BASE'" in self.by_line[3]
        assert "'${BASE}'" in self.by_line[4]
        assert "variable reference" in self.by_line[3]
        assert "variable reference" in self.by_line[4]

    def test_continuation_joined(self):
        """`FROM \\` + next line is one instruction, reported at its first line."""
        assert "'node:20'" in self.by_line[5]

    def test_copy_from_image(self):
        """COPY --from=<image> pulls the image when it is not a stage."""
        assert self.by_line[8].startswith("COPY --from 'python:3.11'")

    def test_all_integrity(self):
        assert all(f.integrity for f in self.findings)


class TestDockerfileStageTracking:
    def test_stage_used_before_declaration_is_an_image(self):
        """A stage name is only visible to instructions after its FROM ... AS."""
        findings = _run_dockerfile("FROM base\nFROM python{} AS base\n".format(_DIGEST))
        assert [f.line for f in findings] == [1]

    def test_stage_names_case_insensitive(self):
        """Docker stage names are case-insensitive."""
        findings = _run_dockerfile(
            "FROM python{} AS Builder\nFROM builder\nCOPY --from=BUILDER /a /b\n".format(
                _DIGEST
            )
        )
        assert findings == []

    def test_copy_from_undeclared_bare_word(self):
        """An undeclared bare --from value resolves to an image, not a stage."""
        findings = _run_dockerfile("FROM scratch\nCOPY --from=busybox /bin/sh /sh\n")
        assert len(findings) == 1
        assert "'busybox'" in findings[0].message

    def test_copy_from_pinned_image(self):
        findings = _run_dockerfile(
            "FROM scratch\nCOPY --from=busybox{} /bin/sh /sh\n".format(_DIGEST)
        )
        assert findings == []

    def test_add_from_and_other_flags(self):
        """--from is found among other leading flags, on ADD as well as COPY."""
        findings = _run_dockerfile(
            "FROM scratch\nADD --chown=1:1 --from=busybox:1.36 /a /b\n"
        )
        assert len(findings) == 1
        assert findings[0].message.startswith("ADD --from 'busybox:1.36'")


class TestDockerfileRunMount:
    def test_mount_from_image(self):
        findings = _run_dockerfile(
            "FROM scratch\nRUN --mount=type=bind,from=python:3.11,target=/p ls /p\n"
        )
        assert len(findings) == 1
        assert "RUN --mount from 'python:3.11'" in findings[0].message

    def test_mount_without_from(self):
        findings = _run_dockerfile(
            "FROM scratch\nRUN --mount=type=cache,target=/root/.cache pip install x\n"
        )
        assert findings == []

    def test_mount_quoted_csv_field(self):
        """BuildKit reads --mount options as CSV, so a field may be quoted."""
        findings = _run_dockerfile(
            'FROM scratch\nRUN --mount=type=bind,"from=python:3.11",target=/x ls\n'
        )
        assert len(findings) == 1
        assert "'python:3.11'" in findings[0].message

    def test_mount_in_shell_command_ignored(self):
        """A --mount inside the RUN command text is not a Dockerfile mount."""
        findings = _run_dockerfile(
            "FROM scratch\nRUN docker run --mount=type=bind,from=x,target=/y img\n"
        )
        assert findings == []


class TestDockerfileContinuation:
    def test_comment_lines_inside_continuation_dropped(self):
        findings = _run_dockerfile("FROM \\\n# base image\n\n  python:3.11 AS app\n")
        assert len(findings) == 1
        assert findings[0].line == 1
        assert "'python:3.11'" in findings[0].message

    def test_escape_directive_backtick(self):
        """`# escape=`` makes backtick the continuation character."""
        findings = _run_dockerfile(
            "# escape=`\nFROM `\n  mcr.microsoft.com/windows/servercore:ltsc2022\n"
        )
        assert len(findings) == 1
        assert "servercore:ltsc2022" in findings[0].message

    def test_from_scratch_still_skipped(self):
        assert _run_dockerfile("FROM scratch\n") == []


class TestDockerfileBuildKitParsing:
    """The instruction splitter must agree with BuildKit, or an image hides."""

    def test_image_name_split_across_lines(self):
        """BuildKit appends the continuation as-is: `alp\\` + `ine` is `alpine`."""
        findings = _run_dockerfile("FROM alp\\\nine\n")
        assert len(findings) == 1
        assert "'alpine'" in findings[0].message

    def test_unknown_directive_ends_directive_parsing(self):
        """After an unknown `# key=value`, a later `# escape=` is only a comment."""
        findings = _run_dockerfile("# foo=bar\n# escape=`\nRUN echo `\nFROM alpine\n")
        assert [f.line for f in findings] == [4]

    def test_blank_line_ends_directive_parsing(self):
        findings = _run_dockerfile("\n# escape=`\nRUN echo `\nFROM alpine\n")
        assert [f.line for f in findings] == [4]

    def test_byte_order_mark_stripped(self):
        """A UTF-8 BOM must not hide a FROM on line 1."""
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, "Dockerfile"), "wb") as fh:
                fh.write(b"\xef\xbb\xbfFROM alpine\n")
            findings = DockerfileChecker().check({d: {"Dockerfile"}}, d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        assert len(findings) == 1
        assert "'alpine'" in findings[0].message

    def test_heredoc_body_is_not_instructions(self):
        findings = _run_dockerfile(
            "FROM scratch\nCOPY <<EOF /inner/Dockerfile\nFROM alpine\nEOF\n"
        )
        assert findings == []

    def test_instructions_after_heredoc_checked(self):
        findings = _run_dockerfile(
            "FROM scratch\nRUN <<EOF\necho hi\nEOF\nFROM alpine\n"
        )
        assert [f.line for f in findings] == [5]

    def test_multiple_and_dash_heredocs(self):
        """`<<-` strips leading tabs from the delimiter line; bodies are consumed in order."""
        findings = _run_dockerfile(
            'FROM scratch\nCOPY <<-"A" <<B /x/\nFROM a\n\tA\nFROM b\nB\nFROM alpine\n'
        )
        assert [f.line for f in findings] == [7]


class TestDockerfileSyntaxDirective:
    def test_unpinned_frontend_flagged(self):
        """`# syntax=` pulls and runs a BuildKit frontend image."""
        findings = _run_dockerfile("# syntax=docker/dockerfile:1\nFROM scratch\n")
        assert len(findings) == 1
        assert findings[0].line == 1
        assert findings[0].message.startswith("# syntax 'docker/dockerfile:1'")

    def test_pinned_frontend_accepted(self):
        findings = _run_dockerfile(
            "# syntax=docker/dockerfile:1{}\nFROM scratch\n".format(_DIGEST)
        )
        assert findings == []

    def test_frontend_is_never_a_stage(self):
        """A stage alias cannot stand in for the frontend image."""
        findings = _run_dockerfile(
            "# syntax=builder\nFROM python{} AS builder\n".format(_DIGEST)
        )
        assert [f.line for f in findings] == [1]

    def test_syntax_after_unknown_directive_is_a_comment(self):
        findings = _run_dockerfile("# foo=bar\n# syntax=docker/dockerfile:1\n")
        assert findings == []


class TestDockerfileOnbuild:
    def test_onbuild_copy_from_image(self):
        findings = _run_dockerfile("FROM scratch\nONBUILD COPY --from=alpine /a /b\n")
        assert len(findings) == 1
        assert "COPY --from 'alpine'" in findings[0].message

    def test_onbuild_run_mount_from_image(self):
        findings = _run_dockerfile(
            "FROM scratch\nONBUILD RUN --mount=type=bind,from=alpine,target=/a ls\n"
        )
        assert len(findings) == 1

    def test_onbuild_copy_from_stage(self):
        findings = _run_dockerfile(
            "FROM python{} AS base\nONBUILD COPY --from=base /a /b\n".format(_DIGEST)
        )
        assert findings == []


class TestDockerfilePatterns:
    def test_containerfile_checked(self):
        findings = _run_dockerfile("FROM alpine\n", fname="Containerfile")
        assert len(findings) == 1

    def test_dot_dockerfile_checked(self):
        findings = _run_dockerfile("FROM alpine\n", fname="api.dockerfile")
        assert len(findings) == 1

    def test_capitalised_dot_dockerfile_checked(self):
        """fnmatch is case-sensitive on POSIX, so both spellings are listed."""
        findings = _run_dockerfile("FROM alpine\n", fname="api.Dockerfile")
        assert len(findings) == 1

    def test_patterns_listed(self):
        patterns = DockerfileChecker.patterns
        for p in ("Dockerfile*", "Containerfile*", "*.dockerfile", "*.Dockerfile"):
            assert p in patterns


class TestDockerfilePlatformFlag:
    def _run(self, content):
        # type: (str) -> list
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "Dockerfile")
            with open(path, "w") as fh:
                fh.write(content)
            index = {d: {"Dockerfile"}}
            return DockerfileChecker().check(index, d)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_from_with_platform(self):
        """FROM --platform=linux/amd64 python:3.11 should be flagged (no digest)."""
        findings = self._run("FROM --platform=linux/amd64 python:3.11\n")
        assert len(findings) == 1, (
            "Expected 1 finding for unpinned platform image, got {}: {}".format(
                len(findings), [f.message for f in findings]
            )
        )
        assert "python:3.11" in findings[0].message
        assert findings[0].checker == "dockerfile"

    def test_from_with_platform_and_digest(self):
        """FROM --platform=linux/amd64 python:3.11@sha256:abc... should produce 0 findings."""
        findings = self._run(
            "FROM --platform=linux/amd64 "
            "python:3.11@sha256:abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890\n"
        )
        assert findings == [], (
            "Digest-pinned platform image should produce 0 findings, got: {}".format(
                [f.message for f in findings]
            )
        )


# ---------------------------------------------------------------------------
# Compose
# ---------------------------------------------------------------------------


class TestComposeGood:
    def test_sha256_pinned_no_findings(self):
        findings = _check_compose("compose_good")
        assert findings == []

    def test_empty_index_no_findings(self):
        assert _check_empty(ComposeChecker()) == []


class TestComposeBad:
    def setup_method(self):
        self.findings = _check_compose("compose_bad")

    def test_one_finding(self):
        assert len(self.findings) == 1, (
            "Expected 1 finding for nginx:1.25.0, got {}: {}".format(
                len(self.findings), [f.message for f in self.findings]
            )
        )

    def test_nginx_flagged(self):
        assert "nginx:1.25.0" in self.findings[0].message

    def test_has_one_finding(self):
        assert len(self.findings) == 1

    def test_checker_name(self):
        assert self.findings[0].checker == "compose"

    def test_path_is_relative(self):
        assert not os.path.isabs(self.findings[0].path)

    def test_line_number_positive(self):
        assert self.findings[0].line > 0


class TestComposeBuildDirective:
    def test_build_section_not_image_not_flagged(self):
        """build: lines should not be matched as image: lines."""
        findings = _check_compose("compose_build")
        assert findings == [], (
            "build: directive should not produce findings, got: {}".format(
                [f.message for f in findings]
            )
        )


class TestComposeAltFilename:
    def test_compose_yml_filename(self):
        """compose.yml (without docker-) should match compose*.yml pattern."""
        findings = _check_compose("compose_alt", fname="compose.yml")
        assert findings == []


class TestDockerfileHeredocAndEscapeEdgeCases:
    """Codex review of #10: literal `<<EOF` arguments and escaped escapes."""

    def test_json_form_argument_is_not_a_heredoc(self):
        findings = _run_dockerfile('FROM scratch\nRUN ["echo", "<<EOF"]\nFROM alpine\n')
        assert [f.line for f in findings] == [3]

    def test_quoted_argument_is_not_a_heredoc(self):
        findings = _run_dockerfile("FROM scratch\nRUN echo '<<EOF'\nFROM alpine\n")
        assert [f.line for f in findings] == [3]

    def test_heredoc_inside_word_is_not_an_opener(self):
        findings = _run_dockerfile("FROM scratch\nRUN echo a<<EOF\nFROM alpine\n")
        assert [f.line for f in findings] == [3]

    def test_unterminated_heredoc_reported(self):
        findings = _run_dockerfile("FROM scratch\nRUN <<EOF\necho hi\nFROM alpine\n")
        assert len(findings) == 1
        assert findings[0].line == 2
        assert "heredoc <<EOF is never closed" in findings[0].message

    def test_heredoc_delimiter_with_punctuation(self):
        """Codex #10 follow-up: the delimiter is the whole shell word, `EOF.txt`."""
        findings = _run_dockerfile(
            "FROM scratch\nCOPY <<EOF.txt /file\nhello\nEOF.txt\n"
        )
        assert findings == []

    def test_heredoc_with_file_descriptor(self):
        """Codex #10 follow-up: `3<<EOF` opens a heredoc; its body is not instructions."""
        findings = _run_dockerfile(
            "FROM scratch\nRUN true 3<<EOF\nFROM alpine\nEOF\nFROM busybox\n"
        )
        assert [f.line for f in findings] == [5]

    def test_partly_quoted_heredoc_delimiter(self):
        """Quotes are removed from the delimiter: `<<E"O"F` is closed by `EOF`."""
        findings = _run_dockerfile(
            'FROM scratch\nRUN cat <<E"O"F\nFROM a\nEOF\nFROM alpine\n'
        )
        assert [f.line for f in findings] == [5]

    def test_escaped_trailing_backslash_ends_the_instruction(self):
        """A line ending in two backslashes ends in an escaped backslash, not a continuation."""
        findings = _run_dockerfile("FROM scratch\nRUN echo \\\\\nFROM alpine\n")
        assert [f.line for f in findings] == [3]

    def test_escaped_trailing_backtick_ends_the_instruction(self):
        findings = _run_dockerfile(
            "# escape=`\nFROM scratch\nRUN echo ``\nFROM alpine\n"
        )
        assert [f.line for f in findings] == [4]

    def test_single_trailing_backslash_still_continues(self):
        findings = _run_dockerfile("FROM \\\n  alpine\n")
        assert "'alpine'" in findings[0].message
        assert [f.line for f in findings] == [1]


def _compose_text(text):
    # type: (str) -> list
    d = tempfile.mkdtemp()
    try:
        with open(os.path.join(d, "compose.yaml"), "w") as fh:
            fh.write(text)
        return [
            (f.line, f.message)
            for f in ComposeChecker().check({d: {"compose.yaml"}}, d)
        ]
    finally:
        shutil.rmtree(d, ignore_errors=True)


class TestComposeYamlForms:
    """Review repro L3: flow mappings bypassed the line regex; digests unchecked."""

    def test_flow_mapping_repro(self):
        findings = _check_compose("compose_flow")
        assert [(f.line, f.message) for f in findings] == [
            (1, "image 'nginx:1.25' is not pinned with @sha256: digest")
        ]

    def test_malformed_digest(self):
        assert _compose_text("services:\n  w:\n    image: nginx@sha256:abc\n") == [
            (
                3,
                "image 'nginx@sha256:abc' has a malformed @sha256: digest; expected"
                " 64 lowercase hex characters at the end of the reference",
            )
        ]

    def test_uppercase_digest_is_malformed(self):
        findings = _compose_text(
            "services:\n  w:\n    image: nginx@sha256:" + "A1B2C3D4" * 8 + "\n"
        )
        assert "malformed @sha256: digest" in findings[0][1]

    def test_variables(self):
        findings = _compose_text(
            "services:\n"
            "  a:\n    image: ${IMAGE}\n"
            "  b:\n    image: ${REGISTRY}/app" + _DIGEST + "\n"
            '  c:\n    image: "${IMG:-nginx' + _DIGEST + '}"\n'
        )
        assert [line for line, _ in findings] == [3, 7]
        assert all("variable reference" in m for _, m in findings)

    def test_anchored_image_checked_where_defined(self):
        findings = _compose_text(
            "x-base: &base\n  image: busybox:1\n"
            "services:\n  a:\n    <<: *base\n  b:\n    image: *missing\n"
        )
        assert findings == [
            (2, "image 'busybox:1' is not pinned with @sha256: digest"),
            (7, "image '*missing' is a YAML alias pinstack could not resolve"),
        ]

    def test_labels_and_environment_are_data(self):
        findings = _compose_text(
            "services:\n  a:\n    image: nginx" + _DIGEST + "\n"
            "    labels:\n      image: fake\n    environment:\n      image: fake\n"
        )
        assert findings == []
